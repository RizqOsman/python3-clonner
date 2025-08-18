# cloner.py
import os
import time
import asyncio
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright

from .utils import mkdir, extract_and_replace_data_uri
from .handlers import create_response_handler, handle_request
from .crawler import auto_scroll_lazy, crawl_additional_links
from .rewriter import rewrite_html_links


INIT_STEALTH_SCRIPT = r"""
// --- Stealth bits ---
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
if (navigator.permissions && navigator.permissions.query) {
  const originalQuery = navigator.permissions.query;
  navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
// Block Service Worker registration (some sites swap CSS via SW)
if (navigator.serviceWorker && navigator.serviceWorker.register) {
  try {
    navigator.serviceWorker.register = new Proxy(navigator.serviceWorker.register, {
      apply() { return Promise.resolve({}); }
    });
  } catch (_) {}
}

// ---- Capture blob: CSS ----
(() => {
  const origCreate = URL.createObjectURL;
  const store = {};
  window.__blobStoreText = store;
  URL.createObjectURL = function(blob) {
    const url = origCreate.call(URL, blob);
    try {
      const reader = new FileReader();
      reader.onload = () => { store[url] = reader.result; };
      // try text first; fallback to dataURL if text fails
      if (blob.type && blob.type.includes("text")) {
        reader.readAsText(blob);
      } else {
        reader.readAsText(blob); // many CSS blobs come as text/plain
      }
    } catch (e) {}
    return url;
  };
})();
"""

INLINE_CSS_SCRIPT = r"""
// Inline all <link rel="stylesheet"> (including blob:) into <style>
async function fetchText(url) {
  try {
    const res = await fetch(url, { credentials: 'include' });
    return await res.text();
  } catch (e) {
    return null;
  }
}
function normalizeUrl(href) {
  try { return new URL(href, location.href).href; } catch (e) { return null; }
}

// Expand simple @import statements (one-level)
async function expandImports(cssText, baseUrl) {
  const importRegex = /@import\s+(?:url\()?['"]?([^'")]+)['"]?\)?\s*([^;]*);/gi;
  let result = cssText;
  const tasks = [];
  const seen = new Set();
  let m;
  while ((m = importRegex.exec(cssText)) !== null) {
    const raw = m[0];
    const href = m[1];
    if (!href) continue;
    const abs = normalizeUrl(new URL(href, baseUrl).href);
    if (!abs || seen.has(abs)) continue;
    seen.add(abs);
    tasks.push((async () => {
      const t = await fetchText(abs);
      if (t) {
        result = result.replace(raw, "\n/* inlined @import "+abs+" */\n"+t+"\n/* end import */\n");
      }
    })());
  }
  await Promise.allSettled(tasks);
  return result;
}

(async () => {
  const links = Array.from(document.querySelectorAll('link[rel="stylesheet"]'));
  for (const link of links) {
    const href = link.getAttribute('href') || '';
    // blob: case
    if (href.startsWith('blob:')) {
      const css = (window.__blobStoreText && window.__blobStoreText[href]) || '';
      if (css) {
        const style = document.createElement('style');
        style.setAttribute('data-inlined-from', href);
        style.textContent = css;
        link.replaceWith(style);
        continue;
      }
    }
    // normal URL case
    const abs = normalizeUrl(href);
    if (!abs) continue;
    let css = await fetchText(abs);
    if (!css) continue;
    // try to expand simple @import
    css = await expandImports(css, abs);
    const style = document.createElement('style');
    style.setAttribute('data-inlined-from', abs);
    style.textContent = css;
    link.replaceWith(style);
  }
})();
"""


async def clone_page(url: str, output_dir: str, full_load: bool, total_timeout_ms: int, headless: bool, crawl_internal: bool = False):
    """Main function to clone a web page"""
    mkdir(output_dir)
    start_time = time.time()
    end_time = start_time + (total_timeout_ms / 1000)

    async with async_playwright() as pw:
        # Build a robust context to handle CSP + UA quirks (FB, etc.)
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-web-security"]
        try:
            browser = await pw.chromium.launch(
                headless=headless,
                channel="chrome",
                args=launch_args
            )
        except Exception:
            print("⚠️ Chrome not found, using bundled Chromium instead")
            browser = await pw.chromium.launch(
                headless=headless,
                args=launch_args
            )

        context = await browser.new_context(
            bypass_csp=True,  # let us inline CSS/scripts
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            java_script_enabled=True,
            color_scheme="light",
            timezone_id="Asia/Jakarta",
            accept_downloads=True
        )

        # Set common headers (some CDNs serve different CSS by Accept-Language)
        await context.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8"
        })

        # Init stealth & blob capture BEFORE any navigation
        await context.add_init_script(INIT_STEALTH_SCRIPT)

        page = await context.new_page()

        # Intercept & save responses (assets, css, fonts, etc.)
        handle_response = await create_response_handler(page, output_dir)
        page.on("response", handle_response)

        print(f"⏱ Total capture time: {total_timeout_ms} ms ({total_timeout_ms/1000:.0f} seconds)")
        print(f"🌐 Opening {url}...")

        # Always use networkidle for heavy SPAs (FB keeps churning; still workable on login page)
        wait_mode = "networkidle"
        await page.route("**/*", handle_request)
        await page.goto(url, wait_until=wait_mode, timeout=0)

        # Try to dismiss common cookie modals quickly (optional best effort)
        try:
            await page.locator('button:has-text("Allow all"), button:has-text("Allow All")').first.click(timeout=1500)
        except Exception:
            pass

        # Scroll to trigger lazy stuff
        await auto_scroll_lazy(page)

        # Give extra time for async style/script injections
        await page.wait_for_timeout(3500)

        # Inline all stylesheets (regular + blob) directly into DOM
        await page.add_script_tag(content=INLINE_CSS_SCRIPT)
        # Wait a tick for inlining to finish
        await page.wait_for_timeout(500)

        if crawl_internal:
            print("🔍 Searching and downloading additional links...")
            await crawl_additional_links(page, url, output_dir)
        else:
            print("🚫 Internal link crawling disabled")

        # Keep the tap open until end_time to catch late imports
        remaining_time = end_time - time.time()
        if remaining_time > 0:
            print(f"⏱ Waiting {int(remaining_time)} seconds to capture additional resources...")
            while time.time() < end_time:
                await asyncio.sleep(1)

        parsed_url = urlparse(url)
        domain_dir = os.path.join(output_dir, parsed_url.netloc)
        html_path = os.path.join(domain_dir, "index.html")
        os.makedirs(domain_dir, exist_ok=True)

        # Final HTML snapshot (after inlining)
        html_content = await page.content()

        # Extract data: URIs to files for portability
        embedded_dir = os.path.join(domain_dir, "assets", "html", "embedded")
        html_content = extract_and_replace_data_uri(
            html_content,
            embedded_dir,
            "html_embedded"
        )

        # Rewrite residual links (imgs, scripts, etc.) to local paths
        html_content = rewrite_html_links(html_content, url, domain_dir)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"📄 HTML saved: {html_path}")

        print("ℹ️ Stylesheets were inlined to avoid CSP/CORS/`blob:` issues (good for Facebook-like sites).")
        print("\n✅ Resource & HTML capture completed!")
        await browser.close()

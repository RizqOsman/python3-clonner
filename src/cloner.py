import os
import time
import asyncio
from urllib.parse import urlparse
from playwright.async_api import async_playwright

from .utils import mkdir, extract_and_replace_data_uri
from .handlers import create_response_handler, handle_request
from .crawler import auto_scroll_lazy, crawl_additional_links
from .rewriter import rewrite_html_links

# ===== Main Function =====
async def clone_page(url: str, output_dir: str, full_load: bool, total_timeout_ms: int, headless: bool, crawl_internal=False):
    """Fungsi utama untuk mengkloning halaman web"""
    mkdir(output_dir)
    start_time = time.time()
    end_time = start_time + (total_timeout_ms / 1000)

    async with async_playwright() as pw:
        # Jika chrome tidak tersedia, gunakan chromium
        try:
            browser = await pw.chromium.launch(headless=headless, channel="chrome", args=["--no-sandbox"])
        except Exception:
            print("⚠️ Chrome tidak ditemukan, menggunakan Chromium sebagai gantinya")
            browser = await pw.chromium.launch(headless=headless, args=["--no-sandbox"])
        
        page = await browser.new_page()

        # Register event handler
        handle_response = await create_response_handler(page, output_dir)
        page.on("response", handle_response)

        print(f"⏱ Total waktu capture: {total_timeout_ms} ms ({total_timeout_ms/1000:.0f} detik)")
        print(f"🌐 Membuka {url}...")

        wait_mode = "networkidle" if full_load else "domcontentloaded"
        await page.route("**/*", handle_request)
        await page.goto(url, wait_until=wait_mode, timeout=0)

        # Auto-scroll
        await auto_scroll_lazy(page)
        
        # Cari dan unduh link tambahan jika opsi diaktifkan
        if crawl_internal:
            print("🔍 Mencari dan mengunduh link tambahan...")
            await crawl_additional_links(page, url, output_dir)
        else:
            print("🚫 Crawling link internal dinonaktifkan")

        # Loop sampai total timeout tercapai
        remaining_time = end_time - time.time()
        if remaining_time > 0:
            print(f"⏱ Menunggu {int(remaining_time)} detik untuk menangkap resource tambahan...")
            while time.time() < end_time:
                await asyncio.sleep(1)

        # Simpan HTML terakhir
        parsed_url = urlparse(url)
        domain_dir = os.path.join(output_dir, parsed_url.netloc)
        html_path = os.path.join(domain_dir, "index.html")
        os.makedirs(domain_dir, exist_ok=True)

        html_content = await page.content()
        # Simpan data URI dalam folder domain/assets/html/embedded
        embedded_dir = os.path.join(domain_dir, "assets", "html", "embedded")
        html_content = extract_and_replace_data_uri(
            html_content,
            embedded_dir,
            "html_embedded"  # Prefix dengan jenis aset html
        )
        
        # Rewrite semua link di halaman utama
        html_content = rewrite_html_links(html_content, url, domain_dir)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"📄 HTML disimpan: {html_path}")
        
        # Tidak perlu membuat file redirect di root karena kita hanya ingin file HTML di folder domain
        # Jika Anda ingin membuat file redirect kembali, hapus komentar pada kode berikut
        """
        # Buat file index.html di root output_dir untuk redirect ke halaman utama
        root_index_path = os.path.join(output_dir, "index.html")
        
        # Buat redirect relatif ke folder domain
        with open(root_index_path, "w", encoding="utf-8") as f:
            redirect_path = f"{parsed_url.netloc}/index.html"
            f.write(f"<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="refresh" content="0; url={redirect_path}" />
    <title>Redirecting to {parsed_url.netloc}</title>
</head>
<body>
    <p>Redirecting to <a href="{redirect_path}">{parsed_url.netloc}</a>...</p>
</body>
</html>")
        print(f"📄 Index redirect disimpan: {root_index_path}")
        """
        # Tampilkan pesan bahwa hanya file HTML utama yang disimpan
        print(f"ℹ️ Hanya file HTML utama yang disimpan di folder domain")

        print("\n✅ Selesai tangkap resource & HTML!")
        await browser.close()

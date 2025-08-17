import asyncio
from urllib.parse import urlparse
from .utils import url_to_local_path

# ===== Auto-scroll helper =====
async def auto_scroll(page):
    """Auto scroll untuk memuat konten lazy load"""
    await page.evaluate("""async () => {
        await new Promise(resolve => {
            let totalHeight = 0;
            const distance = 200;
            const timer = setInterval(() => {
                const scrollHeight = document.body.scrollHeight;
                window.scrollBy(0, distance);
                totalHeight += distance;
                if(totalHeight >= scrollHeight){
                    clearInterval(timer);
                    resolve();
                }
            }, 100);
        });
    }""")

# ===== Auto-scroll + lazy load capture =====
async def auto_scroll_lazy(page, delay=0.5, max_scrolls=50):
    """Scroll otomatis + tangkap lazy request"""
    for i in range(max_scrolls):
        await page.evaluate("window.scrollBy(0, window.innerHeight);")
        await asyncio.sleep(delay)
        # Bisa juga trigger hover atau interaksi tambahan di sini
        # Misal klik "load more" jika ada
        buttons = await page.query_selector_all("button, a")
        for btn in buttons:
            try:
                text = (await btn.inner_text()).lower()
                if "load more" in text or "lihat selengkapnya" in text:
                    await btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                continue

# ===== Crawl aset tambahan =====
async def crawl_additional_links(page, base_url, output_dir):
    """Mencari dan mengunduh link tambahan yang mungkin terlewat"""
    try:
        # Dapatkan semua link di halaman
        links = await page.evaluate("""() => {
            const results = [];
            // Collect links from a tags
            document.querySelectorAll('a[href]').forEach(a => {
                results.push({type: 'a', url: a.href});
            });
            // Collect links from other tags that might have URLs
            const srcElements = document.querySelectorAll('img[src], script[src], link[href], iframe[src], source[src]');
            srcElements.forEach(el => {
                const attr = el.hasAttribute('src') ? 'src' : 'href';
                results.push({type: el.tagName.toLowerCase(), url: el[attr]});
            });
            return results;
        }""")
        
        # Filter links (hanya ambil dari domain yang sama atau subdomain)
        base_domain = urlparse(base_url).netloc
        internal_links = []
        
        for item in links:
            try:
                link_url = item['url']
                if not link_url or link_url.startswith('javascript:') or link_url.startswith('data:') or link_url.startswith('#'):
                    continue
                    
                link_parsed = urlparse(link_url)
                # Jika domain sama atau subdomain, tambahkan ke daftar
                if link_parsed.netloc == base_domain or link_parsed.netloc.endswith('.' + base_domain):
                    internal_links.append(link_url)
            except Exception as e:
                print(f"⚠️ Error processing link {item['url']}: {e}")
        
        print(f"🔍 Menemukan {len(internal_links)} link internal untuk diunduh")
        
        # Untuk setiap link internal, cek apakah sudah diunduh, jika belum unduh
        for link in internal_links:
            if link not in url_to_local_path:
                try:
                    print(f"⏬ Mengunduh link tambahan: {link}")
                    # Buka tab baru untuk mengunduh
                    new_page = await page.context.new_page()
                    await new_page.goto(link, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(1)  # Tunggu sebentar untuk resource di-load
                    await new_page.close()
                except Exception as e:
                    print(f"⚠️ Error mengunduh link {link}: {e}")
                    
    except Exception as e:
        print(f"⚠️ Error crawling additional links: {e}")

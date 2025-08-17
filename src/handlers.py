import os
from .utils import hash_path, extract_and_replace_data_uri, url_to_local_path
from .rewriter import rewrite_html_links, rewrite_css_urls

# ===== Fetch fallback =====
async def fetch_fallback(page, url):
    """Fallback untuk fetch jika normal response.body() gagal"""
    try:
        data = await page.evaluate(
            """async url => {
                try {
                    const r = await fetch(url);
                    const b = await r.arrayBuffer();
                    return Array.from(new Uint8Array(b));
                } catch(e){ return null; }
            }""",
            url
        )
        if data:
            return bytes(data)
    except Exception:
        pass
    return None

# ===== Di handle_response =====
async def create_response_handler(page, output_dir):
    """Buat handler untuk response"""
    async def handle_response(response):
        try:
            content_type = (response.headers.get("content-type") or "").lower()
            try:
                body = await response.body()
            except Exception:
                print(f"⚠️ Normal fetch failed, fallback for: {response.url}")
                body = await fetch_fallback(page, response.url)
                if not body:
                    print(f"❌ Tidak bisa fetch: {response.url}")
                    return

            # Dapatkan domain dari URL target (bukan dari response URL)
            from urllib.parse import urlparse
            
            # Gunakan domain dari URL target, bukan dari response
            # Ini memastikan semua aset disimpan di folder domain yang sama
            # seperti 'x.com' terlepas dari domain asalnya (abs.twimg.com, api.x.com, dll)
            target_domain = urlparse(page.url).netloc
            
            # Tentukan subfolder berdasarkan jenis konten
            asset_type = "misc"  # Default folder untuk tipe yang tidak dikenali
            
            if "text/html" in content_type:
                asset_type = "html"
            elif "text/css" in content_type:
                asset_type = "css"
            elif "text/javascript" in content_type or "application/javascript" in content_type:
                asset_type = "js"
            elif "image/" in content_type:
                asset_type = "images"
            elif "font/" in content_type or "application/font" in content_type or ".woff" in response.url or ".ttf" in response.url:
                asset_type = "fonts"
            elif "video/" in content_type:
                asset_type = "videos"
            elif "audio/" in content_type:
                asset_type = "audio"
            elif "application/json" in content_type:
                asset_type = "json"
                
            # Simpan aset di dalam folder domain/assets/[tipe_aset]
            local_rel_path = os.path.join(target_domain, "assets", asset_type, hash_path(response.url, content_type))
            local_path = os.path.join(output_dir, local_rel_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # Simpan ke mapping URL to local path
            url_to_local_path[response.url] = local_path

            if "text/html" in content_type:
                text_content = body.decode("utf-8", errors="ignore")
                # Simpan data URI dalam folder domain/assets/tipe_aset/embedded
                embedded_dir = os.path.join(os.path.dirname(local_path), "embedded")
                text_content = extract_and_replace_data_uri(
                    text_content,
                    embedded_dir,
                    f"{asset_type}_embedded"  # Prefix dengan jenis aset
                )
                # Rewrite HTML links
                base_url = response.url
                text_content = rewrite_html_links(text_content, base_url, os.path.dirname(local_path))
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(text_content)
            elif "text/css" in content_type:
                text_content = body.decode("utf-8", errors="ignore")
                # Simpan data URI dalam folder domain/assets/tipe_aset/embedded
                embedded_dir = os.path.join(os.path.dirname(local_path), "embedded")
                text_content = extract_and_replace_data_uri(
                    text_content,
                    embedded_dir,
                    f"{asset_type}_embedded"  # Prefix dengan jenis aset
                )
                # Rewrite CSS URLs
                base_url = response.url
                text_content = rewrite_css_urls(text_content, base_url, os.path.dirname(local_path))
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(text_content)
            else:
                with open(local_path, "wb") as f:
                    f.write(body)

            print(f"📥 Saved: {local_path}")

        except Exception as e:
            print(f"⚠️ Error save file: {e}")
            
    return handle_response

async def handle_request(route, request):
    """Handle request dan filter yang tidak dibutuhkan"""
    url = request.url
    # Skip beberapa file yang tidak perlu (manifest, tracking, iklan)
    skip_patterns = [
        "manifest.json", 
        "google-analytics.com", 
        "analytics.", 
        "tracker.", 
        "tracking.", 
        "adservice.", 
        "pagead", 
        "doubleclick.net"
    ]
    
    for pattern in skip_patterns:
        if pattern in url:
            print(f"🚫 Skip: {url}")
            await route.abort()  # Jangan di-fetch
            return
            
    # Lanjutkan untuk semua resource lainnya
    await route.continue_()

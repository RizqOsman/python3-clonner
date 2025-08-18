import os
from .utils import hash_path, extract_and_replace_data_uri, url_to_local_path
from .rewriter import rewrite_html_links, rewrite_css_urls
from urllib.parse import urlparse

async def fetch_fallback(page, url):
    """Fallback fetch method if normal response.body() fails"""
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


async def create_response_handler(page, output_dir):
    """Create handler for responses"""
    async def handle_response(response):
        try:
            content_type = (response.headers.get("content-type") or "").lower()
            try:
                body = await response.body()
            except Exception:
                print(f"⚠️ Normal fetch failed, falling back for: {response.url}")
                body = await fetch_fallback(page, response.url)
                if not body:
                    print(f"❌ Cannot fetch: {response.url}")
                    return

            target_domain = urlparse(page.url).netloc
            
            
            asset_type = "misc"  # Default folder for unrecognized types
            
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
                
            
            # Save assets in domain/assets/[asset_type] folder
            local_rel_path = os.path.join(target_domain, "assets", asset_type, hash_path(response.url, content_type))
            local_path = os.path.join(output_dir, local_rel_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # Store in URL to local path mapping
            url_to_local_path[response.url] = local_path

            if "text/html" in content_type:
                text_content = body.decode("utf-8", errors="ignore")
                
                # Save data URIs in domain/assets/asset_type/embedded folder
                embedded_dir = os.path.join(os.path.dirname(local_path), "embedded")
                text_content = extract_and_replace_data_uri(
                    text_content,
                    embedded_dir,
                    f"{asset_type}_embedded"  # Prefix with asset type
                )
                
                base_url = response.url
                text_content = rewrite_html_links(text_content, base_url, os.path.dirname(local_path))
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(text_content)
            elif "text/css" in content_type:
                text_content = body.decode("utf-8", errors="ignore")
                
                embedded_dir = os.path.join(os.path.dirname(local_path), "embedded")
                text_content = extract_and_replace_data_uri(
                    text_content,
                    embedded_dir,
                    f"{asset_type}_embedded"  
                )
                
                base_url = response.url
                text_content = rewrite_css_urls(text_content, base_url, os.path.dirname(local_path))
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(text_content)
            else:
                with open(local_path, "wb") as f:
                    f.write(body)

            print(f"📥 Saved: {local_path}")

        except Exception as e:
            print(f"⚠️ Error saving file: {e}")
            
    return handle_response

async def handle_request(route, request):
    """Handle requests and filter out unnecessary ones"""
    url = request.url
    
    # Skip unnecessary files (manifest, tracking, ads)
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
            await route.abort()  # Don't fetch
            return
            
    # Continue for all other resources
    await route.continue_()

import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .utils import url_to_local_path

def convert_url_to_local(url, base_url, base_dir):
    """Convert URL to local path based on existing mapping"""
    if not url or url.startswith("data:") or url.startswith("javascript:"):
        return url
    
    # Convert relative URL to absolute
    absolute_url = urljoin(base_url, url)
    
    # Check if this URL has been downloaded already
    if absolute_url in url_to_local_path:
        local_path = url_to_local_path[absolute_url]
        # Return path relative to base_dir
        rel_path = os.path.relpath(local_path, base_dir)
        return rel_path.replace('\\', '/')
    
    return url  # If not downloaded, keep original URL

def rewrite_html_links(html_content, base_url, base_dir):
    """Change all links in HTML to local paths"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    
    for a_tag in soup.find_all('a', href=True):
        a_tag['href'] = convert_url_to_local(a_tag['href'], base_url, base_dir)
    
    for img_tag in soup.find_all('img', src=True):
        img_tag['src'] = convert_url_to_local(img_tag['src'], base_url, base_dir)
    
    for link_tag in soup.find_all('link', href=True):
        link_tag['href'] = convert_url_to_local(link_tag['href'], base_url, base_dir)
    
    for script_tag in soup.find_all('script', src=True):
        script_tag['src'] = convert_url_to_local(script_tag['src'], base_url, base_dir)
    
    for iframe_tag in soup.find_all('iframe', src=True):
        iframe_tag['src'] = convert_url_to_local(iframe_tag['src'], base_url, base_dir)
    
    for img_tag in soup.find_all('img', srcset=True):
        srcset = img_tag['srcset']
        parts = srcset.split(',')
        new_srcset_parts = []
        
        for part in parts:
            url_width = part.strip().split(' ')
            if len(url_width) >= 1:
                url = url_width[0]
                local_url = convert_url_to_local(url, base_url, base_dir)
                new_part = local_url + ' ' + ' '.join(url_width[1:])
                new_srcset_parts.append(new_part)
        
        img_tag['srcset'] = ', '.join(new_srcset_parts)
    
    return str(soup)

def rewrite_css_urls(css_content, base_url, base_dir):
    """Change all URLs in CSS to local paths"""
    url_pattern = re.compile(r'url\([\'"]?(.*?)[\'"]?\)')
    
    def replace_url(match):
        url = match.group(1)
        if url.startswith('data:'):
            return f'url({url})'
        
        local_url = convert_url_to_local(url, base_url, base_dir)
        return f'url({local_url})'
    
    return url_pattern.sub(replace_url, css_content)

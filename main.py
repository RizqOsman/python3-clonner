#!/usr/bin/env python3
"""
Website Cloner

Tool for cloning web pages and all their assets, saving them in an organized
folder structure based on domain and asset type.

Usage examples:
  python3 main.py https://example.com output_folder
  python3 main.py https://example.com output_folder --full --timeout 2m
  python3 main.py https://example.com output_folder --no-headless --crawl-internal
"""

import asyncio
import argparse
from src import clone_page, parse_timeout

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clone web pages and all their assets. This tool uses Playwright to capture web pages along with all assets (images, CSS, JavaScript, fonts, etc.) and saves them in an organized folder structure."
    )
    parser.add_argument("url", 
        help="Target URL to clone. Example: https://example.com"
    )
    parser.add_argument("output", 
        help="Output folder where the cloned content will be saved. A folder structure domain/assets/{js,css,images,etc} will be created"
    )
    parser.add_argument("--full", 
        action="store_true", 
        help="Wait until network idle before starting to capture assets. Use this to ensure all dynamic content is loaded."
    )
    parser.add_argument("--timeout", 
        type=parse_timeout, 
        default=60000, 
        help="Total capture time in seconds (s) or minutes (m). Default: 60 seconds. Examples: 30s, 2m"
    )
    parser.add_argument("--no-headless", 
        action="store_true", 
        help="Run the browser with UI for debugging. Useful for visually monitoring the data capture process."
    )
    parser.add_argument("--crawl-internal", 
        action="store_true", 
        help="Crawl and download internal links found on the page. Warning: This can significantly increase processing time and result size."
    )
    args = parser.parse_args()

    asyncio.run(
        clone_page(
            args.url,
            args.output,
            args.full,
            args.timeout,
            not args.no_headless,
            args.crawl_internal
        )
    )

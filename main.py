#!/usr/bin/env python3
import asyncio
import argparse
from src import clone_page, parse_timeout

# ===== Run Script =====
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clone halaman web dan semua assetnya.")
    parser.add_argument("url", help="URL target untuk di-clone")
    parser.add_argument("output", help="Folder output")
    parser.add_argument("--full", action="store_true", help="Tunggu sampai network idle")
    parser.add_argument("--timeout", type=parse_timeout, default=60000, help="Total waktu capture")
    parser.add_argument("--no-headless", action="store_true", help="Jalankan browser dengan UI untuk debug")
    parser.add_argument("--crawl-internal", action="store_true", help="Crawl dan unduh link internal")
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

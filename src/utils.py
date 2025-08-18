import os
import re
import mimetypes
import hashlib
import base64
import filetype
from urllib.parse import urlparse

# Global mapping to track original URLs to local paths
url_to_local_path = {}
DATA_URI_REGEX = re.compile(r'data:([a-zA-Z0-9/+\-.]+);base64,([a-zA-Z0-9+/=]+)')


def hash_path(url: str, content_type: str) -> str:
    """Create a filename from a hash of the URL"""
    ext = mimetypes.guess_extension(content_type.split(";")[0]) or ".bin"
    h = hashlib.sha1(url.encode()).hexdigest()
    return f"{h}{ext}"

def mkdir(path):
    """Create directory if it doesn't exist"""
    os.makedirs(path, exist_ok=True)


def sanitize_path(url: str, content_type: str) -> str:
    """Create a safe path from URL"""
    parsed = urlparse(url.split("#")[0].split("?")[0])
    safe_netloc = re.sub(r"[^a-zA-Z0-9._-]", "_", parsed.netloc)
    safe_path = re.sub(r"[^a-zA-Z0-9._/-]", "_", parsed.path.lstrip("/"))

    if not safe_path or safe_path.endswith("/"):
        safe_path += "index"

    if not os.path.splitext(safe_path)[1]:
        ext = mimetypes.guess_extension(content_type.split(";")[0])
        if ext:
            safe_path += ext
        else:
            safe_path += ".bin"

    return os.path.join(safe_netloc, safe_path)


def parse_timeout(value: str) -> int:
    """Parse timeout string (e.g., '30s', '1m') into milliseconds"""
    value = str(value).lower().strip()
    if value.endswith("ms"):
        return int(value[:-2])
    elif value.endswith("s"):
        return int(value[:-1]) * 1000
    elif value.endswith("m"):
        return int(value[:-1]) * 60 * 1000
    else:
        return int(value) * 1000

def extract_and_replace_data_uri(content: str, base_dir: str, prefix="embedded") -> str:
    """Extract data URIs into separate files and replace with relative paths"""
    os.makedirs(base_dir, exist_ok=True)
    matches = list(DATA_URI_REGEX.finditer(content))

    for i, match in enumerate(matches, start=1):
        mime_type = match.group(1)
        data_b64 = match.group(2)
        ext = mimetypes.guess_extension(mime_type) or ".bin"

        file_name = f"{prefix}_{i}{ext}"
        file_path = os.path.join(base_dir, file_name)

        try:
            with open(file_path, "wb") as f:
                f.write(base64.b64decode(data_b64))
            print(f"📦 Extracted embedded data URI → {file_path}")
            content = content.replace(match.group(0), file_name)
        except Exception as e:
            print(f"⚠️ Error extracting base64: {e}")

    return content


def detect_extension(url: str, content_type: str, data: bytes) -> str:
    """Detect file extension from URL, Content-Type, or magic bytes"""
    ext = os.path.splitext(urlparse(url).path)[1]
    if ext:
        return ext

    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0])
        if guessed:
            return guessed

    kind = filetype.guess(data)
    if kind:
        return f".{kind.extension}"

    return ".bin"

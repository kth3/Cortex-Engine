import re
import hashlib

def strip_frontmatter(content: str) -> str:
    """YAML Frontmatter (--- ... ---) 제거"""
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)

def compute_hash(content: str) -> str:
    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()

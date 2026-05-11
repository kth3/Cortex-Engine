"""Markdown parsing helpers for Cortex skills."""

from __future__ import annotations

import re
from pathlib import Path


def parse_skill_md(skill_md_path: str) -> dict:
    """Parse a skill markdown file into normalized metadata."""
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return {}

    name = ""
    description = ""
    tags: list[str] = []

    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        description_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
        tags_match = re.search(r"^tags:\s*(?:\[)?([^\]\n]*)(?:\])?", frontmatter, re.MULTILINE)

        if name_match:
            name = name_match.group(1).strip().strip('"')
        if description_match:
            description = description_match.group(1).strip().strip('"')
        if tags_match:
            tags = [tag.strip().strip('"').strip("'") for tag in tags_match.group(1).split(",") if tag.strip()]

    if not name:
        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        name = h1_match.group(1).strip() if h1_match else Path(skill_md_path).stem

    if not description:
        paragraphs = re.findall(r"(?m)^(?!#|---|```|\s*$)(.+)$", content)
        description = " ".join(paragraphs[:3]).strip()[:500]

    if not tags:
        text = f"{name} {description}".lower()
        tags = [keyword for keyword in ["python", "mcp", "agent", "test"] if keyword in text] or ["skill"]

    return {
        "name": name,
        "description": description,
        "path": skill_md_path,
        "tags": tags,
        "content_preview": content[:2000],
        "full_content": content,
    }

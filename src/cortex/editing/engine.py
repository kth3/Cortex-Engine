#!/usr/bin/env python3
"""
Editing engine for hashline reads, strict replacements, and edit event recording.
"""
import os
import re
import hashlib


def _safe_resolve(workspace: str, file_path: str) -> str:
    """Resolve a workspace-relative file path and reject path traversal."""
    if os.path.isabs(file_path):
        raise PermissionError(
            f"Path traversal blocked: absolute path '{file_path}' is not allowed"
        )

    full_path = os.path.abspath(os.path.join(workspace, file_path))
    workspace_abs = os.path.abspath(workspace)

    if not full_path.startswith(workspace_abs + os.sep) and full_path != workspace_abs:
        raise PermissionError(
            f"Path traversal blocked: '{file_path}' escapes workspace '{workspace}'"
        )
    return full_path


def read_with_hash(workspace, file_path):
    full_path = _safe_resolve(workspace, file_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(full_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    output = []
    for i, line in enumerate(lines):
        line_content = line.rstrip("\n")
        h = hashlib.sha256(line_content.encode()).hexdigest()[:6]
        output.append(f"{i+1:4} | {h} | {line_content}")

    return "\n".join(output)


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy content matching."""
    lines = text.split("\n")
    normalized_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            normalized_lines.append(re.sub(r'\s+', ' ', stripped))
    return "\n".join(normalized_lines)


def _find_fuzzy_match(content: str, old_content: str) -> tuple[int, int] | None:
    """Find an old_content range while ignoring whitespace-only differences."""
    old_normalized = _normalize_whitespace(old_content)
    old_norm_lines = old_normalized.split("\n")

    if not old_norm_lines or not old_norm_lines[0]:
        return None

    content_lines = content.split("\n")
    content_norm_lines = [re.sub(r'\s+', ' ', line.strip()) for line in content_lines]
    window_size = len(old_content.split("\n"))

    for i in range(len(content_norm_lines) - window_size + 1):
        window = content_norm_lines[i:i + window_size]
        window_filtered = [l for l in window if l.strip()]
        old_filtered = [l for l in old_norm_lines if l.strip()]

        if window_filtered == old_filtered:
            start_idx = sum(len(content_lines[j]) + 1 for j in range(i))
            end_idx = sum(len(content_lines[j]) + 1 for j in range(i + window_size))
            if end_idx > len(content) + 1:
                end_idx = len(content)
            return (start_idx, end_idx)

    return None


def strict_replace(workspace, file_path, old_content, new_content):
    """Replace content exactly, falling back to whitespace-normalized fuzzy matching."""
    full_path = _safe_resolve(workspace, file_path)
    if not os.path.exists(full_path):
        return {"error": f"File not found: {file_path}"}

    with open(full_path, "r", encoding="utf-8") as f:
        current = f.read()

    if old_content in current:
        updated = current.replace(old_content, new_content, 1)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
        return {"success": True, "match_type": "exact"}

    match_range = _find_fuzzy_match(current, old_content)
    if match_range:
        start_idx, end_idx = match_range
        updated = current[:start_idx] + new_content + "\n" + current[end_idx:]
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
        return {
            "success": True,
            "match_type": "fuzzy",
            "note": "Matched with whitespace normalization (indent/spacing differences were ignored).",
        }

    return {
        "error": "Content mismatch",
        "reason": "The code block was not found even with fuzzy matching.",
        "tip": "Re-read the file with hashes and ensure the old_content is semantically identical.",
    }


EMPTY_FILE_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ALLOWED_SOURCES = frozenset({"cortex_mcp"})


def normalize_event_path(workspace: str, path: str):
    """Normalize file_edit_events.file_path and reject paths outside workspace."""
    from pathlib import Path

    if not workspace or not path:
        return None
    try:
        ws_abs = Path(workspace).resolve()
        target = Path(path)
        if not target.is_absolute():
            target = (ws_abs / target).resolve()
        else:
            target = target.resolve()
        try:
            rel = target.relative_to(ws_abs)
        except ValueError:
            return None
        rel_posix = rel.as_posix()
        if rel_posix.startswith("..") or rel_posix == "":
            return None
        if os.name == "nt":
            rel_posix = rel_posix.lower()
        return rel_posix
    except (OSError, ValueError):
        return None


def canonical_sources(existing, new_source: str) -> str:
    """Return canonical event_sources form with validation."""
    if new_source not in ALLOWED_SOURCES:
        raise ValueError(f"UNKNOWN_SOURCE:{new_source}")
    parts = set(filter(None, (existing or "").split(",")))
    parts.add(new_source)
    invalid = parts - ALLOWED_SOURCES
    if invalid:
        raise ValueError(f"UNKNOWN_SOURCE:{','.join(sorted(invalid))}")
    return ",".join(sorted(parts))


def upsert_edit_event(conn, *, file_path, before_hash, after_hash, session_id,
                      event_source, tool_name=None, line_range=None,
                      edit_summary=None, now_iso=None):
    """Insert or update a file_edit_events row by the deduplication key."""
    import datetime

    if now_iso is None:
        now_iso = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with conn:
        row = conn.execute(
            "SELECT id, event_sources FROM file_edit_events "
            "WHERE file_path=? AND before_hash=? AND after_hash=? AND session_id=?",
            (file_path, before_hash, after_hash, session_id),
        ).fetchone()
        if row is None:
            new_sources = canonical_sources(None, event_source)
            conn.execute(
                """
                INSERT INTO file_edit_events
                  (file_path, before_hash, after_hash, line_range, tool_name,
                   event_sources, session_id, edit_summary, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (file_path, before_hash, after_hash, line_range, tool_name,
                 new_sources, session_id, edit_summary, now_iso, now_iso),
            )
        else:
            existing_sources = row["event_sources"] if hasattr(row, "keys") else row[1]
            row_id = row["id"] if hasattr(row, "keys") else row[0]
            new_sources = canonical_sources(existing_sources, event_source)
            conn.execute(
                """
                UPDATE file_edit_events
                SET event_sources=?, updated_at=?, tool_name=?,
                    line_range=COALESCE(?, line_range),
                    edit_summary=COALESCE(?, edit_summary)
                WHERE id=?
                """,
                (new_sources, now_iso, tool_name, line_range, edit_summary, row_id),
            )


def record_edit_event(conn, *, workspace, file_path, before_content, after_content,
                      session_id, event_source="cortex_mcp", tool_name=None,
                      line_range=None, edit_summary=None, now_iso=None):
    """Record an edit event using whole-file before/after SHA-256 hashes."""
    normalized_path = normalize_event_path(workspace, file_path)
    if normalized_path is None:
        raise ValueError(f"Invalid edit event path outside workspace: {file_path}")

    before_hash = hashlib.sha256(before_content.encode("utf-8")).hexdigest()
    after_hash = hashlib.sha256(after_content.encode("utf-8")).hexdigest()
    upsert_edit_event(
        conn,
        file_path=normalized_path,
        before_hash=before_hash,
        after_hash=after_hash,
        session_id=session_id,
        event_source=event_source,
        tool_name=tool_name,
        line_range=line_range,
        edit_summary=edit_summary,
        now_iso=now_iso,
    )

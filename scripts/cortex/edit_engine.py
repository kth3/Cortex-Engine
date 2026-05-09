#!/usr/bin/env python3
"""
edit_engine.py - Hashline 기반 정밀 편집 엔진
코드 매칭 검증 및 안전한 치환 로직 전담.
소형 모델(0.6B)의 공백/들여쓰기 실수를 보정하는 퍼지 매칭 지원.
"""
import os
import re
import hashlib


def _safe_resolve(workspace: str, file_path: str) -> str:
    """Fix #6: 경로 이탈(Path Traversal) 방지.
    
    os.path.join은 file_path가 절대 경로이면 workspace를 무시하므로,
    반드시 정규화 후 workspace 범위 내인지 검증합니다.
    """
    # 절대 경로는 즉시 거부 (workspace 외부 파일 접근 시도)
    if os.path.isabs(file_path):
        raise PermissionError(
            f"Path traversal blocked: absolute path '{file_path}' is not allowed"
        )
    
    full_path = os.path.abspath(os.path.join(workspace, file_path))
    workspace_abs = os.path.abspath(workspace)
    
    # ../를 통한 workspace 경계 탈출 검증
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
    """공백/탭/줄바꿈 차이를 정규화하여 퍼지 비교용 문자열로 변환.
    
    - 각 라인의 앞뒤 공백(Indent 포함)을 제거
    - 빈 줄을 제거
    - 연속 공백을 단일 공백으로 통합
    소형 모델이 들여쓰기/공백에서 범하는 잦은 실수를 흡수합니다.
    """
    lines = text.split("\n")
    normalized_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:  # 빈 줄 무시
            # 연속 공백 → 단일 공백
            normalized_lines.append(re.sub(r'\s+', ' ', stripped))
    return "\n".join(normalized_lines)


def _find_fuzzy_match(content: str, old_content: str) -> tuple[int, int] | None:
    """원본 내용에서 old_content와 공백 정규화 기준으로 일치하는 영역을 찾습니다.
    
    Returns:
        (start_idx, end_idx) 또는 None
    """
    old_normalized = _normalize_whitespace(old_content)
    old_norm_lines = old_normalized.split("\n")
    
    if not old_norm_lines or not old_norm_lines[0]:
        return None
    
    content_lines = content.split("\n")
    content_norm_lines = [re.sub(r'\s+', ' ', line.strip()) for line in content_lines]
    
    # 원본 라인 수 기준으로 윈도우 크기 결정 (빈 줄 포함 정확한 범위 특정)
    window_size = len(old_content.split("\n"))
    
    for i in range(len(content_norm_lines) - window_size + 1):
        window = content_norm_lines[i:i + window_size]
        # 빈 라인을 필터링한 비교
        window_filtered = [l for l in window if l.strip()]
        old_filtered = [l for l in old_norm_lines if l.strip()]
        
        if window_filtered == old_filtered:
            # 원본 content에서 해당 라인 범위의 정확한 바이트 위치 계산
            start_idx = sum(len(content_lines[j]) + 1 for j in range(i))
            end_idx = sum(len(content_lines[j]) + 1 for j in range(i + window_size))
            # 마지막 줄에는 \n이 없을 수 있으므로 보정
            if end_idx > len(content) + 1:
                end_idx = len(content)
            return (start_idx, end_idx)
    
    return None


def strict_replace(workspace, file_path, old_content, new_content):
    """퍼지 매칭 지원 정밀 편집.
    
    1단계: 정확한 문자열 매칭 (기존 동작)
    2단계: 실패 시 공백/들여쓰기 차이를 무시하는 퍼지 매칭 시도
    소형 모델(0.6B)의 Indent/공백 실수를 자동 보정합니다.
    """
    full_path = _safe_resolve(workspace, file_path)
    if not os.path.exists(full_path):
        return {"error": f"File not found: {file_path}"}
    
    with open(full_path, "r", encoding="utf-8") as f:
        current = f.read()
    
    # 1단계: 정확한 매칭 (최우선)
    if old_content in current:
        updated = current.replace(old_content, new_content, 1)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
        return {"success": True, "match_type": "exact"}
    
    # 2단계: 퍼지 매칭 (공백/들여쓰기 차이 무시)
    match_range = _find_fuzzy_match(current, old_content)
    if match_range:
        start_idx, end_idx = match_range
        updated = current[:start_idx] + new_content + "\n" + current[end_idx:]
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
        return {
            "success": True, 
            "match_type": "fuzzy",
            "note": "Matched with whitespace normalization (indent/spacing differences were ignored)."
        }
    
    return {
        "error": "Content mismatch",
        "reason": "The code block was not found even with fuzzy matching.",
        "tip": "Re-read the file with hashes and ensure the old_content is semantically identical."
    }


# ==============================================================================
# file_edit_events 적재 헬퍼 (Stage 0)
# Cortex MCP 내부 편집 이벤트 기록용
# ==============================================================================

# 신규 파일 생성 시 before_hash 값 (sha256 of empty bytes)
EMPTY_FILE_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# 허용 event source enum — 오타 방지
ALLOWED_SOURCES = frozenset({"cortex_mcp"})


def normalize_event_path(workspace: str, path: str):
    """file_edit_events.file_path 적재용 정규화 함수.

    dedup 키 일관성을 위해 적재 직전 항상 통과시킨다.
    실패 시 None 반환 (호출자가 적재 거부 결정).

    정규화 단계:
      1. Path.resolve() → 절대 경로
      2. 워크스페이스 상대 경로
      3. forward slash 통일 (Path.as_posix())
      4. Windows 환경에서 lower() (case-insensitive dedup)
      5. 워크스페이스 외부(`..` 시작) 거부
    """
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
            # 워크스페이스 외부
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
    """event_sources canonical form (정렬 + 중복 차단 + enum guard).

    Args:
      existing: 기존 event_sources 컬럼 값 또는 None
      new_source: 추가하려는 source — ALLOWED_SOURCES 내 값

    Raises:
      ValueError("UNKNOWN_SOURCE:..."): new_source 또는 기존 값에 미허용 source 존재
    """
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
    """file_edit_events 테이블에 INSERT 또는 UPSERT.

    동일 dedup 키(file_path, before_hash, after_hash, session_id)가 존재하면
    event_sources를 canonical 누적, updated_at 갱신, created_at은 보존.

    호출자는 file_path가 normalize_event_path로 정규화된 값임을 보장해야 한다.
    """
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
            # row는 sqlite3.Row 또는 tuple. 두 경우 모두 정상 처리
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
    """실제 파일 내용 기반으로 MCP 편집 이벤트를 기록한다.

    old_content/new_content 인자는 치환 대상 조각일 뿐이며, fuzzy match나 부분 치환에서는
    파일 전체 상태를 대표하지 못한다. 운영 lineage는 "편집 전 파일 전체"에서 "편집 후 파일
    전체"로 이동한 사실을 추적해야 하므로, 호출자는 디스크에서 읽은 전체 내용을 넘기고
    이 함수가 그 전체 내용의 SHA-256을 dedup 키로 사용한다.
    """
    normalized_path = normalize_event_path(workspace, file_path)
    if normalized_path is None:
        # 정규화 실패를 조용히 무시하면 편집은 성공했는데 감사 로그만 누락되는 상태가 된다.
        # 호출자가 event_log_error로 노출할 수 있도록 명시적으로 실패시킨다.
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

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

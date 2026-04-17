#!/usr/bin/env python3
"""
edit_engine.py - Hashline 기반 정밀 편집 엔진
코드 매칭 검증 및 안전한 치환 로직 전담.
"""
import os
import hashlib

def read_with_hash(workspace, file_path):
    full_path = os.path.join(workspace, file_path)
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

def strict_replace(workspace, file_path, old_content, new_content):
    full_path = os.path.join(workspace, file_path)
    if not os.path.exists(full_path):
        return {"error": f"File not found: {file_path}"}
    
    with open(full_path, "r", encoding="utf-8") as f:
        current = f.read()
    
    if old_content not in current:
        return {
            "error": "Content mismatch",
            "reason": "The exact code block was not found in the file.",
            "tip": "Re-read the file with hashes and ensure the old_content is identical."
        }
    
    updated = current.replace(old_content, new_content, 1)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(updated)
    
    return {"success": True}

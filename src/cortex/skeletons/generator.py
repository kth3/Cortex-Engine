"""
파일 또는 노드의 스켈레톤(시그니처 + 독스트링)을 생성하여 토큰을 절약합니다.
"""
import os
import json
import subprocess
from pathlib import Path


def get_node_skeleton(node_dict, detail="standard"):
    """단일 노드의 스켈레톤 생성"""
    signature = node_dict.get("signature", "")
    docstring = (
        node_dict.get("raw_body", "").strip().split("\n")[0]
        if "raw_body" in node_dict
        else ""
    )

    if detail == "minimal":
        return signature
    if detail == "standard":
        if (
            docstring.startswith('"""')
            or docstring.startswith("'''")
            or docstring.startswith("/*")
            or docstring.startswith("//")
        ):
            return f"{signature}\n    {docstring}"
        return signature

    body = node_dict.get("raw_body", "")
    lines = body.split("\n")
    return "\n".join(lines[:5]) + " ... (truncated)"


def generate_file_skeleton(nodes, detail="standard"):
    """파일 내의 모든 노드를 순서대로 스켈레톤화하여 결합"""
    sorted_nodes = sorted(nodes, key=lambda x: x.get("start_line", 0))
    parts = []
    for node in sorted_nodes:
        skel = get_node_skeleton(node, detail)
        if skel:
            parts.append(str(skel))
    return "\n\n".join(parts)


def _rust_watcher_binary():
    name = "cortex-watcher.exe" if os.name == "nt" else "cortex-watcher"
    repo_root = Path(__file__).resolve().parents[3]
    for profile in ("release", "debug"):
        candidate = repo_root / "rust" / "target" / profile / name
        if candidate.exists():
            return candidate
    return repo_root / "rust" / "target" / "release" / name


def generate_skeleton(workspace, file_path, detail="standard"):
    abs_path = os.path.join(workspace, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(abs_path):
        return f"File not found: {abs_path}"

    binary = _rust_watcher_binary()
    proc = subprocess.run(
        [str(binary), "parse-file", "--file", abs_path, "--rel", file_path],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout or "{}")
    nodes = result.get("nodes", [])
    return generate_file_skeleton(nodes, detail)

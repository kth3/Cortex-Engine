"""Skill discovery, synchronization, and retrieval."""

from __future__ import annotations

import gc
import json
import os
import re
import sys
import time
from pathlib import Path

from cortex import vector_engine as ve
from cortex.db import get_connection, init_schema, to_rel_path
from cortex.skills.parser import parse_skill_md


class SkillManager:
    def __init__(self, workspace: str):
        self.workspace = workspace
        conn = get_connection(workspace)
        try:
            init_schema(conn)
        finally:
            conn.close()

    def _skill_dirs(self) -> list[str]:
        return [
            os.path.join(self.workspace, ".agents", "skills"),
            os.path.join(self.workspace, ".agents", "knowledge", "skills"),
        ]

    def _skill_files(self) -> list[Path]:
        skill_files: list[Path] = []
        for directory in self._skill_dirs():
            if os.path.isdir(directory):
                skill_files.extend(Path(directory).rglob("*.md"))
        return skill_files

    @staticmethod
    def _skill_key(skill_path: Path) -> str:
        if skill_path.name.lower() == "skill.md":
            return f"skill::{skill_path.parent.name}"
        return f"skill::{skill_path.stem}"

    def _load_skill_records(self, skill_files: list[Path]) -> tuple[dict[str, dict], int, list[str]]:
        skill_info_map: dict[str, dict] = {}
        skipped = 0
        errors: list[str] = []

        for skill_path in skill_files:
            try:
                info = parse_skill_md(str(skill_path))
                if not info:
                    skipped += 1
                    continue

                skill_key = self._skill_key(skill_path)
                name = info.get("name", skill_path.stem)
                description = info.get("description", "")
                tags = info.get("tags", ["skill"])
                preview = info.get("content_preview", "")
                rel_path = to_rel_path(str(skill_path), self.workspace)

                skill_info_map[skill_key] = {
                    "key": skill_key,
                    "name": name,
                    "description": description,
                    "content": f"[SKILL] {name}\n설명: {description}\n경로: {rel_path}\n\n{preview}",
                    "tags_json": json.dumps(tags, ensure_ascii=False),
                    "tags": tags,
                }
            except Exception as exc:
                errors.append(f"{skill_path}: {exc}")

        return skill_info_map, skipped, errors

    @staticmethod
    def _existing_skill_vector_state(conn, keys: list[str]) -> dict[str, bool]:
        existing_map: dict[str, bool] = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            placeholders = ",".join(["?"] * len(chunk))
            rows = conn.execute(
                f"SELECT m.key, (v.rowid IS NOT NULL) as has_vec "
                f"FROM memories m "
                f"LEFT JOIN vec_memories v ON v.rowid = m.rowid "
                f"WHERE m.key IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                row_dict = dict(row)
                key = row_dict.get("key")
                if key:
                    existing_map[key] = bool(row_dict.get("has_vec", 0))
        return existing_map

    def _upsert_skill_memories(self, conn, project_id: str, skill_info_map: dict[str, dict]) -> tuple[int, list[dict]]:
        if not skill_info_map:
            return 0, []

        now = int(time.time())
        existing_map = self._existing_skill_vector_state(conn, list(skill_info_map.keys()))
        to_insert = []
        to_update = []
        pending_embed = []

        for skill in skill_info_map.values():
            skill_key = skill["key"]
            pending_embed.append({"id": skill_key, "text": f"{skill['name']} {skill['description']}"})
            if skill_key in existing_map:
                to_update.append((skill["content"], skill["tags_json"], now, skill_key))
            else:
                to_insert.append((skill_key, project_id, skill["content"], skill["tags_json"], now, now))

        if to_insert:
            conn.executemany(
                "INSERT INTO memories (key, project_id, category, content, tags, created_at, updated_at) "
                "VALUES (?, ?, 'skill', ?, ?, ?, ?)",
                to_insert,
            )
        if to_update:
            conn.executemany(
                "UPDATE memories SET content=?, tags=?, updated_at=? WHERE key=?",
                to_update,
            )
        conn.commit()
        return len(skill_info_map), pending_embed

    @staticmethod
    def _vectorize_skills(conn, workspace: str, vector_items: list[dict]) -> int:
        if not vector_items:
            return 0

        try:
            from cortex.vectorizer import detect_gpu
            use_gpu = detect_gpu()
        except ImportError:
            use_gpu = False

        sys.stderr.write(f"[skill_manager] Vectorizing {len(vector_items)} skills (GPU={use_gpu})...\n")

        from cortex.indexer_utils import get_tuning_params
        from tqdm import tqdm

        batch_size = get_tuning_params().get("batch_size", 16)
        embedded = 0

        vec_conn = get_connection(workspace)
        try:
            for i in tqdm(range(0, len(vector_items), batch_size), desc="Skills Embedding", unit="batch"):
                batch = vector_items[i:i + batch_size]
                texts = [item["text"] for item in batch]
                embeddings = ve.get_embeddings(texts, use_gpu=use_gpu)

                ids = [item["id"] for item in batch]
                placeholders = ",".join(["?"] * len(ids))
                rowid_map = {
                    row[0]: row[1]
                    for row in vec_conn.execute(
                        f"SELECT key, rowid FROM memories WHERE key IN ({placeholders})", ids
                    ).fetchall()
                }
                delete_params = [(rowid_map[item["id"]],) for item in batch if item["id"] in rowid_map]
                insert_params = [
                    (rowid_map[item["id"]], embedding.tobytes())
                    for item, embedding in zip(batch, embeddings)
                    if item["id"] in rowid_map
                ]
                try:
                    vec_conn.executemany("DELETE FROM vec_memories WHERE rowid = ?", delete_params)
                except Exception:
                    pass
                vec_conn.executemany("INSERT INTO vec_memories(rowid, embedding) VALUES (?, ?)", insert_params)
                vec_conn.commit()
                embedded += len(batch)
                if use_gpu:
                    import torch
                    torch.cuda.empty_cache()
                gc.collect()
        finally:
            vec_conn.close()

        sys.stderr.write(f"[skill_manager] Vector indexing done: {embedded} skills embedded.\n")
        return embedded

    def sync_skills(self, project_id: str) -> dict:
        conn = get_connection(self.workspace)
        synced = 0
        embedded = 0
        errors: list[str] = []
        skipped = 0

        try:
            skill_info_map, skipped, errors = self._load_skill_records(self._skill_files())
            synced, pending_embed = self._upsert_skill_memories(conn, project_id, skill_info_map)
            pending_ids = {item["id"] for item in pending_embed}
            vector_items = [
                {"id": key, "text": f"{skill['name']} {skill['description']}", "meta": {"name": skill["name"], "tags": skill["tags"]}}
                for key, skill in skill_info_map.items()
                if key in pending_ids
            ]
            embedded = self._vectorize_skills(conn, self.workspace, vector_items)
        except Exception as exc:
            errors.append(f"동기화 중 에러: {exc}")
        finally:
            conn.close()

        return {"synced": synced, "skipped": skipped, "errors": errors, "embedded": embedded}

    def search_skills(self, project_id: str, query: str, limit: int = 5) -> list:
        conn = get_connection(self.workspace)
        try:
            fts_scored, fts_data = self._search_skills_fts(conn, query, limit)
            sem_scored = self._search_skills_vector(conn, query, limit, fts_data)
            all_keys = sorted(
                set(fts_scored) | set(sem_scored),
                key=lambda key: fts_scored.get(key, 0.0) + sem_scored.get(key, 0.0),
                reverse=True,
            )[:limit]

            return [
                {
                    "key": key,
                    "name": key.replace("skill::", ""),
                    "summary": fts_data[key].get("content", "")[:400] if key in fts_data else "",
                    "tags": json.loads(fts_data[key].get("tags") or "[]") if key in fts_data else [],
                    "score": round(fts_scored.get(key, 0.0) + sem_scored.get(key, 0.0), 6),
                }
                for key in all_keys
                if key in fts_data
            ]
        finally:
            conn.close()

    @staticmethod
    def _search_skills_fts(conn, query: str, limit: int) -> tuple[dict[str, float], dict[str, dict]]:
        tokens = [f'"{token}"*' for token in re.split(r'[\s\-_.,/]+', query) if len(token) >= 2]
        fts_query = " OR ".join(tokens) if tokens else "*"
        try:
            rows = conn.execute(
                "SELECT m.* FROM memories_fts f JOIN memories m ON m.rowid = f.rowid "
                "WHERE memories_fts MATCH ? AND m.category = 'skill' ORDER BY rank LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        except Exception:
            rows = []

        scored: dict[str, float] = {}
        data: dict[str, dict] = {}
        for rank, row in enumerate(rows):
            row_dict = dict(row)
            scored[row_dict["key"]] = 1.0 / (rank + 60)
            data[row_dict["key"]] = row_dict
        return scored, data

    @staticmethod
    def _search_skills_vector(conn, query: str, limit: int, fts_data: dict[str, dict]) -> dict[str, float]:
        scored: dict[str, float] = {}
        try:
            query_vec = ve.get_embeddings([query])[0]
            rows = conn.execute(
                "SELECT m.key, m.content, m.tags "
                "FROM vec_memories v "
                "JOIN memories m ON m.rowid = v.rowid "
                "WHERE v.embedding MATCH ? AND k = ?",
                (query_vec.tobytes(), limit * 2),
            ).fetchall()

            missing_keys: list[str] = []
            for rank, row in enumerate(rows):
                row_dict = dict(row)
                item_id = row_dict["key"]
                scored[item_id] = 1.0 / (rank + 60)
                if item_id not in fts_data:
                    missing_keys.append(item_id)

            for i in range(0, len(missing_keys), 900):
                chunk = missing_keys[i:i + 900]
                placeholders = ",".join(["?"] * len(chunk))
                db_rows = conn.execute(f"SELECT * FROM memories WHERE key IN ({placeholders})", chunk).fetchall()
                for db_row in db_rows:
                    row_dict = dict(db_row)
                    fts_data[row_dict["key"]] = row_dict
        except Exception as exc:
            sys.stderr.write(f"[skill_manager] Vector search skipped: {exc}\n")
        return scored

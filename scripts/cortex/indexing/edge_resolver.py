"""Resolve parser-produced unresolved edge targets."""

from __future__ import annotations

from collections import defaultdict

from cortex.logger import get_logger
from cortex.indexing.queries import (
    SELECT_UNRESOLVED_EDGES_SQL,
    UPDATE_EDGE_TARGET_ID_SQL,
    select_node_id_fqn_by_name_sql,
    select_edge_id_lang_by_target_sql,
    select_node_id_name_by_name_lang_sql,
    select_node_id_name_by_name_sql,
)

log = get_logger("indexing.edge_resolver")


def _resolve_fqn_edges(conn, fqn_edges: list[tuple[int, str]]) -> list[tuple[str, int]]:
    lookup_keys: dict[tuple[str, str], list[int]] = {}
    for edge_id, target_id in fqn_edges:
        dotted_fqn = target_id[len(UNRESOLVED_FQN_PREFIX):]
        parts = dotted_fqn.rsplit(".", 1)
        if len(parts) != 2:
            continue
        module_path = parts[0].replace(".", "/") + ".py"
        class_name = parts[1]
        lookup_keys.setdefault((module_path, class_name), []).append(edge_id)

    unique_names = list({key[1] for key in lookup_keys})
    if not unique_names:
        return []

    placeholders = ",".join("?" * len(unique_names))
    rows = conn.execute(
        select_node_id_fqn_by_name_sql(placeholders),
        unique_names,
    ).fetchall()

    updates: list[tuple[str, int]] = []
    for node_id, fqn in rows:
        for (module_path, class_name), edge_ids in lookup_keys.items():
            if f"{module_path}::{class_name}" in fqn:
                updates.extend((node_id, edge_id) for edge_id in edge_ids)
    return updates


def _source_language_map(conn, edge_ids: list[int]) -> dict[int, str]:
    src_lang_map: dict[int, str] = {}
    for index in range(0, len(edge_ids), 900):
        batch = edge_ids[index:index + 900]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            select_edge_id_lang_by_target_sql(placeholders),
            batch,
        ).fetchall()
        for row_id, language in rows:
            src_lang_map[row_id] = language
    return src_lang_map


def _nodes_by_name_lang(conn, lang_to_names: dict[str, set[str]]) -> dict[tuple[str, str], str]:
    node_by_name_lang: dict[tuple[str, str], str] = {}
    for language, names in lang_to_names.items():
        name_list = list(names)
        placeholders = ",".join("?" * len(name_list))
        rows = conn.execute(
            select_node_id_name_by_name_lang_sql(placeholders),
            name_list + [language],
        ).fetchall()
        for node_id, name in rows:
            node_by_name_lang[(name, language)] = node_id
    return node_by_name_lang


def _nodes_by_name_any(conn, names: set[str]) -> dict[str, str]:
    if not names:
        return {}
    name_list = list(names)
    placeholders = ",".join("?" * len(name_list))
    rows = conn.execute(select_node_id_name_by_name_sql(placeholders), name_list).fetchall()
    return {name: node_id for node_id, name in rows}


def _resolve_name_edges(conn, name_edges: list[tuple[int, str]]) -> list[tuple[str, int]]:
    edge_ids = [edge_id for edge_id, _ in name_edges]
    src_lang_map = _source_language_map(conn, edge_ids)

    lang_to_names: dict[str, set[str]] = defaultdict(set)
    no_lang_names: set[str] = set()
    for edge_id, target_id in name_edges:
        name = target_id.split("::")[-1]
        language = src_lang_map.get(edge_id)
        if language:
            lang_to_names[language].add(name)
        else:
            no_lang_names.add(name)

    node_by_name_lang = _nodes_by_name_lang(conn, lang_to_names)
    all_fallback_names = no_lang_names | {name for names in lang_to_names.values() for name in names}
    node_by_name_any = _nodes_by_name_any(conn, all_fallback_names)

    updates: list[tuple[str, int]] = []
    for edge_id, target_id in name_edges:
        name = target_id.split("::")[-1]
        language = src_lang_map.get(edge_id)
        node_id = node_by_name_lang.get((name, language)) if language else None
        if not node_id:
            node_id = node_by_name_any.get(name)
        if node_id:
            updates.append((node_id, edge_id))
    return updates


def resolve_unresolved_edges(conn) -> None:
    """Replace unresolved edge target IDs with resolved node IDs when possible."""
    unresolved = conn.execute(SELECT_UNRESOLVED_EDGES_SQL).fetchall()
    if not unresolved:
        return

    fqn_edges = [(edge_id, target_id) for edge_id, target_id in unresolved if target_id.startswith(UNRESOLVED_FQN_PREFIX)]
    name_edges = [(edge_id, target_id) for edge_id, target_id in unresolved if not target_id.startswith(UNRESOLVED_FQN_PREFIX)]

    updates = _resolve_fqn_edges(conn, fqn_edges)
    updates.extend(_resolve_name_edges(conn, name_edges))

    if updates:
        conn.executemany(UPDATE_EDGE_TARGET_ID_SQL, updates)
        conn.commit()
        log.info("Resolved %d unresolved edges.", len(updates))


__all__ = ["resolve_unresolved_edges"]

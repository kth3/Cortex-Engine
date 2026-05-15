"""Resolve parser-produced unresolved edge targets."""

from __future__ import annotations

from collections import defaultdict

from cortex.logger import get_logger
from cortex.indexing.queries import (
    SELECT_UNRESOLVED_EDGES_SQL,
    UNRESOLVED_FQN_PREFIX,
    UPDATE_EDGE_TARGET_ID_SQL,
    UPDATE_EDGE_STATUS_SQL,
    select_edge_id_lang_by_edge_id_sql,
)

log = get_logger("indexing.edge_resolver")


def _source_language_map(conn, edge_ids: list[int]) -> dict[int, str]:
    src_lang_map: dict[int, str] = {}
    for index in range(0, len(edge_ids), 900):
        batch = edge_ids[index:index + 900]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            select_edge_id_lang_by_edge_id_sql(placeholders),
            batch,
        ).fetchall()
        for row_id, language in rows:
            src_lang_map[row_id] = language
    return src_lang_map


def _target_name(target_id: str, target_name: str | None) -> str:
    return target_name or target_id.split("::")[-1]


def _collect_targets(unresolved: list) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    fqns: set[str] = set()
    for row in unresolved:
        _edge_id, target_id, _edge_type, target_name, _kind_hint, target_fqn_hint = row
        names.add(_target_name(target_id, target_name))
        if target_fqn_hint:
            fqns.add(target_fqn_hint)
        if target_id.startswith(UNRESOLVED_FQN_PREFIX):
            dotted_fqn = target_id[len(UNRESOLVED_FQN_PREFIX):]
            parts = dotted_fqn.rsplit(".", 1)
            if len(parts) == 2:
                names.add(parts[1])
    return names, fqns


def _fetch_candidates(conn, names: set[str], fqns: set[str]) -> list[tuple]:
    candidates: list[tuple] = []
    name_list = list(names)
    for i in range(0, len(name_list), 900):
        batch = name_list[i:i + 900]
        phs = ",".join("?" * len(batch))
        candidates.extend(conn.execute(
            f"SELECT id, name, fqn, language, type FROM nodes WHERE name IN ({phs})", batch,
        ).fetchall())

    fqn_list = list(fqns)
    if fqn_list:
        for i in range(0, len(fqn_list), 900):
            batch = fqn_list[i:i + 900]
            phs = ",".join("?" * len(batch))
            candidates.extend(conn.execute(
                f"SELECT id, name, fqn, language, type FROM nodes WHERE fqn IN ({phs})", batch,
            ).fetchall())
    return candidates


def _build_lookup_maps(candidates: list[tuple]) -> tuple[dict[str, list], dict[str, list]]:
    nodes_by_id = {c[0]: c for c in candidates}
    nodes_by_name: dict[str, list] = defaultdict(list)
    nodes_by_fqn: dict[str, list] = defaultdict(list)
    for c in nodes_by_id.values():
        _n_id, n_name, n_fqn, _n_lang, _n_type = c
        nodes_by_name[n_name].append(c)
        nodes_by_fqn[n_fqn].append(c)
    return nodes_by_name, nodes_by_fqn


def _match_by_fqn_hint(target_fqn_hint: str | None, nodes_by_fqn: dict[str, list]) -> list:
    if target_fqn_hint and target_fqn_hint in nodes_by_fqn:
        return list(nodes_by_fqn[target_fqn_hint])
    return []


def _match_by_dotted_fqn_fallback(target_id: str, nodes_by_name: dict[str, list]) -> list:
    # Python 파서 호환: __unresolved_fqn__::pkg.mod.ClassName 형식의 dotted FQN에서
    # mod_path/cls_name을 추출하여 nodes.fqn에 포함되는 후보를 찾는다.
    if not target_id.startswith(UNRESOLVED_FQN_PREFIX):
        return []
    dotted_fqn = target_id[len(UNRESOLVED_FQN_PREFIX):]
    parts = dotted_fqn.rsplit(".", 1)
    if len(parts) != 2:
        return []
    mod_path = parts[0].replace(".", "/") + ".py"
    cls_name = parts[1]
    expected_substr = f"{mod_path}::{cls_name}"
    return [c for c in nodes_by_name.get(cls_name, []) if expected_substr in c[2]]


def _match_by_kind_hint(name_candidates: list, source_lang: str | None, target_kind_hint: str | None) -> list:
    if not (source_lang and target_kind_hint):
        return []
    return [c for c in name_candidates if c[3] == source_lang and c[4] == target_kind_hint]


def _match_by_language(name_candidates: list, source_lang: str | None) -> list:
    if not source_lang:
        return []
    return [c for c in name_candidates if c[3] == source_lang]


def _resolve_one(row, src_lang_map, nodes_by_name, nodes_by_fqn) -> list:
    edge_id, target_id, _edge_type, target_name, target_kind_hint, target_fqn_hint = row
    source_lang = src_lang_map.get(edge_id)
    name = _target_name(target_id, target_name)

    # Priority 1: exact target_fqn_hint match (with Python dotted FQN fallback).
    matches = _match_by_fqn_hint(target_fqn_hint, nodes_by_fqn)
    if not matches:
        matches = _match_by_dotted_fqn_fallback(target_id, nodes_by_name)
    if matches:
        return matches

    name_candidates = nodes_by_name.get(name, [])

    # Priority 2: language + kind hint + name.
    matches = _match_by_kind_hint(name_candidates, source_lang, target_kind_hint)
    if matches:
        return matches

    # Priority 3: language + name.
    matches = _match_by_language(name_candidates, source_lang)
    if matches:
        return matches

    # Priority 4: name-only fallback.
    return name_candidates


def _apply_updates(conn, resolved_updates: list, ambiguous_updates: list) -> None:
    if resolved_updates:
        conn.executemany(UPDATE_EDGE_TARGET_ID_SQL, resolved_updates)
    if ambiguous_updates:
        conn.executemany(UPDATE_EDGE_STATUS_SQL, ambiguous_updates)
    if resolved_updates or ambiguous_updates:
        conn.commit()
        log.info("Resolved %d edges. %d edges left ambiguous.", len(resolved_updates), len(ambiguous_updates))


def resolve_unresolved_edges(conn) -> None:
    """Replace unresolved edge target IDs with resolved node IDs when possible."""
    unresolved = conn.execute(SELECT_UNRESOLVED_EDGES_SQL).fetchall()
    if not unresolved:
        return

    edge_ids = [row[0] for row in unresolved]
    src_lang_map = _source_language_map(conn, edge_ids)

    names, fqns = _collect_targets(unresolved)
    candidates = _fetch_candidates(conn, names, fqns)
    nodes_by_name, nodes_by_fqn = _build_lookup_maps(candidates)

    resolved_updates: list[tuple] = []
    ambiguous_updates: list[tuple] = []
    for row in unresolved:
        matches = _resolve_one(row, src_lang_map, nodes_by_name, nodes_by_fqn)
        edge_id = row[0]
        if len(matches) == 1:
            resolved_updates.append((matches[0][0], edge_id))
        elif len(matches) > 1:
            ambiguous_updates.append(("ambiguous", edge_id))
            log.debug("Ambiguous resolution for edge %d: %d candidates found.", edge_id, len(matches))

    _apply_updates(conn, resolved_updates, ambiguous_updates)


__all__ = ["resolve_unresolved_edges"]

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

def resolve_unresolved_edges(conn) -> None:
    """Replace unresolved edge target IDs with resolved node IDs when possible."""
    unresolved = conn.execute(SELECT_UNRESOLVED_EDGES_SQL).fetchall()
    if not unresolved:
        return

    edge_ids = [row[0] for row in unresolved]
    src_lang_map = _source_language_map(conn, edge_ids)

    names_to_fetch = set()
    fqns_to_fetch = set()
    
    for row in unresolved:
        edge_id, target_id, edge_type, target_name, target_kind_hint, target_fqn_hint = row
        name = target_name or target_id.split("::")[-1]
        names_to_fetch.add(name)
        if target_fqn_hint:
            fqns_to_fetch.add(target_fqn_hint)
        if target_id.startswith(UNRESOLVED_FQN_PREFIX):
            dotted_fqn = target_id[len(UNRESOLVED_FQN_PREFIX):]
            parts = dotted_fqn.rsplit(".", 1)
            if len(parts) == 2:
                names_to_fetch.add(parts[1])

    candidates = []
    # Fetch by name
    name_list = list(names_to_fetch)
    for i in range(0, len(name_list), 900):
        batch = name_list[i:i+900]
        phs = ",".join("?" * len(batch))
        candidates.extend(conn.execute(f"SELECT id, name, fqn, language, type FROM nodes WHERE name IN ({phs})", batch).fetchall())
    
    # Fetch by fqn hint
    fqn_list = list(fqns_to_fetch)
    if fqn_list:
        for i in range(0, len(fqn_list), 900):
            batch = fqn_list[i:i+900]
            phs = ",".join("?" * len(batch))
            candidates.extend(conn.execute(f"SELECT id, name, fqn, language, type FROM nodes WHERE fqn IN ({phs})", batch).fetchall())

    # Build lookup maps
    nodes_by_id = {c[0]: c for c in candidates}
    nodes_by_name = defaultdict(list)
    nodes_by_fqn = defaultdict(list)
    
    for c in nodes_by_id.values():
        n_id, n_name, n_fqn, n_lang, n_type = c
        nodes_by_name[n_name].append(c)
        nodes_by_fqn[n_fqn].append(c)

    resolved_updates = []
    ambiguous_updates = []
    
    for row in unresolved:
        edge_id, target_id, edge_type, target_name, target_kind_hint, target_fqn_hint = row
        name = target_name or target_id.split("::")[-1]
        source_lang = src_lang_map.get(edge_id)
        
        matches = []
        
        # Priority 1: exact target_fqn_hint match
        if target_fqn_hint and target_fqn_hint in nodes_by_fqn:
            matches = nodes_by_fqn[target_fqn_hint]
            
        # Old Python FQN logic fallback if it starts with UNRESOLVED_FQN_PREFIX
        if not matches and target_id.startswith(UNRESOLVED_FQN_PREFIX):
            dotted_fqn = target_id[len(UNRESOLVED_FQN_PREFIX):]
            parts = dotted_fqn.rsplit(".", 1)
            if len(parts) == 2:
                mod_path = parts[0].replace(".", "/") + ".py"
                cls_name = parts[1]
                expected_substr = f"{mod_path}::{cls_name}"
                for c in nodes_by_name.get(cls_name, []):
                    if expected_substr in c[2]: # fqn is c[2]
                        matches.append(c)

        if not matches:
            # Gather by name
            name_candidates = nodes_by_name.get(name, [])
            
            # Priority 2: language + target_kind_hint + target_name match
            if source_lang and target_kind_hint:
                kind_matches = [c for c in name_candidates if c[3] == source_lang and c[4] == target_kind_hint]
                if kind_matches:
                    matches = kind_matches
                    
            # Priority 3: language + target_name match
            if not matches and source_lang:
                lang_matches = [c for c in name_candidates if c[3] == source_lang]
                if lang_matches:
                    matches = lang_matches
                    
            # Priority 4: name-only fallback
            if not matches:
                matches = name_candidates
                
        # Result logic
        if len(matches) == 1:
            resolved_updates.append((matches[0][0], edge_id))
        elif len(matches) > 1:
            ambiguous_updates.append(("ambiguous", edge_id))
            log.debug("Ambiguous resolution for edge %d: %d candidates found.", edge_id, len(matches))
        else:
            # Leave as unresolved
            pass

    if resolved_updates:
        conn.executemany(UPDATE_EDGE_TARGET_ID_SQL, resolved_updates)
    if ambiguous_updates:
        conn.executemany(UPDATE_EDGE_STATUS_SQL, ambiguous_updates)
        
    if resolved_updates or ambiguous_updates:
        conn.commit()
        log.info("Resolved %d edges. %d edges left ambiguous.", len(resolved_updates), len(ambiguous_updates))

__all__ = ["resolve_unresolved_edges"]

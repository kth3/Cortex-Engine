use std::collections::{HashMap, HashSet};

use anyhow::Result;
use rusqlite::{params, Connection};

use cortex_parsers::UNRESOLVED_FQN_PREFIX;

const UNRESOLVED_EDGE_SQL: &str =
    "SELECT id, target_id, type, target_name, target_kind_hint, target_fqn_hint \
     FROM edges WHERE resolution_status = 'unresolved' OR target_id LIKE '__unresolved%'";
const UPDATE_EDGE_TARGET_ID_SQL: &str =
    "UPDATE OR IGNORE edges SET target_id = ?, resolution_status = 'resolved' WHERE id = ?";
const UPDATE_EDGE_STATUS_SQL: &str =
    "UPDATE OR IGNORE edges SET resolution_status = ? WHERE id = ?";

#[derive(Clone)]
struct Candidate {
    id: String,
    name: String,
    fqn: String,
    language: String,
    kind: String,
}

pub fn resolve_unresolved_edges(conn: &mut Connection) -> Result<()> {
    let tx = conn.transaction()?;
    let unresolved = fetch_unresolved_edges(&tx)?;
    if unresolved.is_empty() {
        tx.commit()?;
        return Ok(());
    }

    let edge_ids: Vec<i64> = unresolved.iter().map(|row| row.edge_id).collect();
    let src_lang_map = source_language_map(&tx, &edge_ids)?;
    let (names, fqns) = collect_targets(&unresolved);
    let candidates = fetch_candidates(&tx, &names, &fqns)?;
    let (nodes_by_name, nodes_by_fqn) = build_lookup_maps(candidates);

    let mut resolved_updates: Vec<(String, i64)> = Vec::new();
    let mut ambiguous_updates: Vec<(String, i64)> = Vec::new();

    for row in &unresolved {
        let matches = resolve_one(row, &src_lang_map, &nodes_by_name, &nodes_by_fqn);
        if matches.len() == 1 {
            resolved_updates.push((matches[0].id.clone(), row.edge_id));
        } else if matches.len() > 1 {
            ambiguous_updates.push(("ambiguous".to_string(), row.edge_id));
            tracing::debug!(
                "Ambiguous resolution for edge {}: {} candidates found.",
                row.edge_id,
                matches.len()
            );
        }
    }

    apply_updates(&tx, &resolved_updates, &ambiguous_updates)?;
    tx.commit()?;

    if !resolved_updates.is_empty() || !ambiguous_updates.is_empty() {
        tracing::info!(
            resolved = resolved_updates.len(),
            ambiguous = ambiguous_updates.len(),
            "Resolved unresolved edges"
        );
    }

    Ok(())
}

#[derive(Clone)]
struct UnresolvedEdge {
    edge_id: i64,
    target_id: String,
    target_name: Option<String>,
    target_kind_hint: Option<String>,
    target_fqn_hint: Option<String>,
}

fn fetch_unresolved_edges(conn: &Connection) -> Result<Vec<UnresolvedEdge>> {
    let mut stmt = conn.prepare(UNRESOLVED_EDGE_SQL)?;
    let rows = stmt.query_map([], |row| {
        Ok(UnresolvedEdge {
            edge_id: row.get(0)?,
            target_id: row.get(1)?,
            target_name: row.get(3)?,
            target_kind_hint: row.get(4)?,
            target_fqn_hint: row.get(5)?,
        })
    })?;
    let mut unresolved = Vec::new();
    for row in rows {
        unresolved.push(row?);
    }
    Ok(unresolved)
}

fn source_language_map(conn: &Connection, edge_ids: &[i64]) -> Result<HashMap<i64, String>> {
    let mut src_lang_map = HashMap::new();
    for batch in edge_ids.chunks(900) {
        let placeholders = vec!["?"; batch.len()].join(",");
        let sql = format!(
            "SELECT e.id, n.language FROM edges e JOIN nodes n ON e.source_id = n.id WHERE e.id IN ({})",
            placeholders
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(batch.iter()), |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        })?;
        for row in rows {
            let (edge_id, language) = row?;
            src_lang_map.insert(edge_id, language);
        }
    }
    Ok(src_lang_map)
}

fn target_name(target_id: &str, target_name: Option<&str>) -> String {
    target_name
        .map(|name| name.to_string())
        .unwrap_or_else(|| target_id.split("::").last().unwrap_or(target_id).to_string())
}

fn collect_targets(unresolved: &[UnresolvedEdge]) -> (HashSet<String>, HashSet<String>) {
    let mut names = HashSet::new();
    let mut fqns = HashSet::new();
    for row in unresolved {
        names.insert(target_name(&row.target_id, row.target_name.as_deref()));
        if let Some(target_fqn_hint) = row.target_fqn_hint.as_deref() {
            fqns.insert(target_fqn_hint.to_string());
        }
        if row.target_id.starts_with(UNRESOLVED_FQN_PREFIX) {
            let dotted_fqn = &row.target_id[UNRESOLVED_FQN_PREFIX.len()..];
            if let Some((_, cls_name)) = dotted_fqn.rsplit_once('.') {
                names.insert(cls_name.to_string());
            }
        }
    }
    (names, fqns)
}

fn fetch_candidates(
    conn: &Connection,
    names: &HashSet<String>,
    fqns: &HashSet<String>,
) -> Result<Vec<Candidate>> {
    let mut by_id: HashMap<String, Candidate> = HashMap::new();
    fetch_candidates_by_field(conn, names, "name", &mut by_id)?;
    fetch_candidates_by_field(conn, fqns, "fqn", &mut by_id)?;
    Ok(by_id.into_values().collect())
}

fn fetch_candidates_by_field(
    conn: &Connection,
    values: &HashSet<String>,
    field: &str,
    by_id: &mut HashMap<String, Candidate>,
) -> Result<()> {
    let list: Vec<String> = values.iter().cloned().collect();
    for batch in list.chunks(900) {
        if batch.is_empty() {
            continue;
        }
        let placeholders = vec!["?"; batch.len()].join(",");
        let sql = format!(
            "SELECT id, name, fqn, language, type FROM nodes WHERE {} IN ({})",
            field, placeholders
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(batch.iter()), |row| {
            Ok(Candidate {
                id: row.get(0)?,
                name: row.get(1)?,
                fqn: row.get(2)?,
                language: row.get(3)?,
                kind: row.get(4)?,
            })
        })?;
        for row in rows {
            let candidate = row?;
            by_id.entry(candidate.id.clone()).or_insert(candidate);
        }
    }
    Ok(())
}

fn build_lookup_maps(candidates: Vec<Candidate>) -> (HashMap<String, Vec<Candidate>>, HashMap<String, Vec<Candidate>>) {
    let mut nodes_by_name: HashMap<String, Vec<Candidate>> = HashMap::new();
    let mut nodes_by_fqn: HashMap<String, Vec<Candidate>> = HashMap::new();
    let mut nodes_by_id: HashMap<String, Candidate> = HashMap::new();

    for candidate in candidates {
        nodes_by_id.entry(candidate.id.clone()).or_insert(candidate);
    }

    for candidate in nodes_by_id.into_values() {
        nodes_by_name
            .entry(candidate.name.clone())
            .or_default()
            .push(candidate.clone());
        nodes_by_fqn
            .entry(candidate.fqn.clone())
            .or_default()
            .push(candidate);
    }

    (nodes_by_name, nodes_by_fqn)
}

fn match_by_fqn_hint(
    target_fqn_hint: Option<&str>,
    nodes_by_fqn: &HashMap<String, Vec<Candidate>>,
) -> Vec<Candidate> {
    target_fqn_hint
        .and_then(|hint| nodes_by_fqn.get(hint))
        .cloned()
        .unwrap_or_default()
}

fn match_by_dotted_fqn_fallback(
    target_id: &str,
    nodes_by_name: &HashMap<String, Vec<Candidate>>,
) -> Vec<Candidate> {
    if !target_id.starts_with(UNRESOLVED_FQN_PREFIX) {
        return Vec::new();
    }

    let dotted_fqn = &target_id[UNRESOLVED_FQN_PREFIX.len()..];
    let Some((mod_path, cls_name)) = dotted_fqn.rsplit_once('.') else {
        return Vec::new();
    };

    let expected_substr = format!("{}::{}", mod_path.replace('.', "/"), cls_name);
    nodes_by_name
        .get(cls_name)
        .into_iter()
        .flat_map(|candidates| candidates.iter())
        .filter(|candidate| candidate.fqn.contains(&expected_substr))
        .cloned()
        .collect()
}

fn match_by_kind_hint(
    name_candidates: &[Candidate],
    source_lang: Option<&str>,
    target_kind_hint: Option<&str>,
) -> Vec<Candidate> {
    let (Some(source_lang), Some(target_kind_hint)) = (source_lang, target_kind_hint) else {
        return Vec::new();
    };
    name_candidates
        .iter()
        .filter(|candidate| candidate.language == source_lang && candidate.kind == target_kind_hint)
        .cloned()
        .collect()
}

fn match_by_language(
    name_candidates: &[Candidate],
    source_lang: Option<&str>,
) -> Vec<Candidate> {
    let Some(source_lang) = source_lang else {
        return Vec::new();
    };
    name_candidates
        .iter()
        .filter(|candidate| candidate.language == source_lang)
        .cloned()
        .collect()
}

fn resolve_one(
    row: &UnresolvedEdge,
    src_lang_map: &HashMap<i64, String>,
    nodes_by_name: &HashMap<String, Vec<Candidate>>,
    nodes_by_fqn: &HashMap<String, Vec<Candidate>>,
) -> Vec<Candidate> {
    let source_lang = src_lang_map.get(&row.edge_id).map(String::as_str);
    let name = target_name(&row.target_id, row.target_name.as_deref());

    let mut matches = match_by_fqn_hint(row.target_fqn_hint.as_deref(), nodes_by_fqn);
    if matches.is_empty() {
        matches = match_by_dotted_fqn_fallback(&row.target_id, nodes_by_name);
    }
    if !matches.is_empty() {
        return matches;
    }

    let name_candidates = nodes_by_name.get(&name).cloned().unwrap_or_default();
    matches = match_by_kind_hint(
        &name_candidates,
        source_lang,
        row.target_kind_hint.as_deref(),
    );
    if !matches.is_empty() {
        return matches;
    }

    matches = match_by_language(&name_candidates, source_lang);
    if !matches.is_empty() {
        return matches;
    }

    name_candidates
}

fn apply_updates(
    conn: &Connection,
    resolved_updates: &[(String, i64)],
    ambiguous_updates: &[(String, i64)],
) -> Result<()> {
    if !resolved_updates.is_empty() {
        let mut stmt = conn.prepare(UPDATE_EDGE_TARGET_ID_SQL)?;
        for (target_id, edge_id) in resolved_updates {
            stmt.execute(params![target_id, edge_id])?;
        }
    }

    if !ambiguous_updates.is_empty() {
        let mut stmt = conn.prepare(UPDATE_EDGE_STATUS_SQL)?;
        for (status, edge_id) in ambiguous_updates {
            stmt.execute(params![status, edge_id])?;
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::resolve_unresolved_edges;
    use cortex_parsers::{EdgeRecord, NodeRecord};
    use rusqlite::{params, Connection};

    fn create_schema(conn: &Connection) -> Result<(), rusqlite::Error> {
        conn.execute_batch(
            r#"
            CREATE TABLE file_cache (
                file_path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                last_indexed_at INTEGER NOT NULL,
                node_count INTEGER DEFAULT 0,
                workspace_id TEXT DEFAULT 'default'
            );
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                fqn TEXT NOT NULL,
                file_path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                signature TEXT,
                return_type TEXT,
                docstring TEXT,
                is_exported INTEGER DEFAULT 1,
                is_async INTEGER DEFAULT 0,
                is_test INTEGER DEFAULT 0,
                raw_body TEXT,
                skeleton_standard TEXT,
                skeleton_minimal TEXT,
                language TEXT NOT NULL,
                module TEXT DEFAULT 'unknown',
                workspace_id TEXT DEFAULT 'default',
                category TEXT DEFAULT 'SOURCE'
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'CALLS',
                target_name TEXT,
                target_kind_hint TEXT,
                target_fqn_hint TEXT,
                resolution_status TEXT DEFAULT 'unresolved',
                resolution_confidence REAL DEFAULT 1.0,
                call_site_line INTEGER,
                confidence REAL DEFAULT 1.0,
                UNIQUE(source_id, target_id, type)
            );
            "#,
        )
    }

    fn insert_node(conn: &Connection, node: &NodeRecord) -> Result<(), rusqlite::Error> {
        conn.execute(
            "INSERT INTO nodes (id, type, name, fqn, file_path, start_line, end_line, signature, return_type, docstring, is_exported, is_async, is_test, raw_body, skeleton_standard, skeleton_minimal, language, module, workspace_id, category) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                node.id,
                node.node_type,
                node.name,
                node.fqn,
                node.file_path,
                node.start_line,
                node.end_line,
                node.signature,
                node.return_type,
                node.docstring,
                node.is_exported,
                node.is_async,
                node.is_test,
                node.raw_body,
                node.skeleton_standard,
                node.skeleton_minimal,
                node.language,
                "mod",
                "ws",
                "SOURCE",
            ],
        )?;
        Ok(())
    }

    fn sample_node(id: &str, name: &str, fqn: &str, language: &str, kind: &str) -> NodeRecord {
        NodeRecord {
            id: id.to_string(),
            node_type: kind.to_string(),
            name: name.to_string(),
            fqn: fqn.to_string(),
            file_path: "src/lib.rs".to_string(),
            start_line: 1,
            end_line: 2,
            signature: Some("fn sample()".to_string()),
            return_type: None,
            docstring: None,
            is_exported: Some(1),
            is_async: Some(0),
            is_test: Some(0),
            raw_body: "fn sample() {}".to_string(),
            skeleton_standard: None,
            skeleton_minimal: None,
            language: language.to_string(),
        }
    }

    fn sample_edge(target_id: &str, target_name: Option<&str>, target_fqn_hint: Option<&str>) -> EdgeRecord {
        EdgeRecord {
            source_id: "source".to_string(),
            target_id: target_id.to_string(),
            edge_type: "CALLS".to_string(),
            target_name: target_name.map(ToString::to_string),
            target_kind_hint: Some("FUNCTION".to_string()),
            target_fqn_hint: target_fqn_hint.map(ToString::to_string),
            call_site_line: Some(5),
            confidence: 1.0,
        }
    }

    #[test]
    fn resolves_unresolved_edge_by_name() -> Result<(), rusqlite::Error> {
        let conn = Connection::open_in_memory()?;
        create_schema(&conn)?;
        insert_node(&conn, &sample_node("source", "source", "pkg/source.py::source", "python", "FUNCTION"))?;
        insert_node(&conn, &sample_node("target", "callee", "pkg/target.py::callee", "python", "FUNCTION"))?;
        let edge = sample_edge("__unresolved__::callee", Some("callee"), None);
        conn.execute(
            "INSERT INTO edges (source_id, target_id, type, target_name, target_kind_hint, target_fqn_hint, resolution_status, resolution_confidence, call_site_line, confidence) VALUES (?, ?, ?, ?, ?, ?, 'unresolved', 1.0, ?, ?)",
            params![
                edge.source_id,
                edge.target_id,
                edge.edge_type,
                edge.target_name,
                edge.target_kind_hint,
                edge.target_fqn_hint,
                edge.call_site_line,
                edge.confidence,
            ],
        )?;

        let mut conn = conn;
        resolve_unresolved_edges(&mut conn).expect("resolve unresolved edges");

        let row: (String, String) = conn.query_row(
            "SELECT target_id, resolution_status FROM edges WHERE source_id = ?1",
            ["source"],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        assert_eq!(row.0, "target");
        assert_eq!(row.1, "resolved");
        Ok(())
    }
}

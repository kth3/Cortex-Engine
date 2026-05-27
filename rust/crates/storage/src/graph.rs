use std::collections::HashMap;
use std::path::Path;

use anyhow::{Context, Result};
use kuzu::{Connection as KuzuConnection, Database, SystemConfig, Value};
use rusqlite::Connection as SqliteConnection;

const UNRESOLVED_TARGET_PREFIX: &str = "__unresolved";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GraphDirection {
    Callers,
    Callees,
    Both,
}

impl GraphDirection {
    pub fn from_str(value: &str) -> Self {
        match value {
            "callers" => Self::Callers,
            "callees" => Self::Callees,
            _ => Self::Both,
        }
    }
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct GraphSyncStats {
    pub nodes: usize,
    pub edges: usize,
}

#[derive(Debug)]
pub struct GraphNodeRow {
    pub id: String,
    pub fqn: String,
    pub name: String,
    pub node_type: String,
    pub file_path: String,
    pub start_line: i64,
    pub language: String,
}

#[derive(Debug)]
pub struct GraphEdgeRow {
    pub source_id: String,
    pub target_id: String,
    pub edge_type: String,
    pub target_name: Option<String>,
    pub target_kind_hint: Option<String>,
    pub target_fqn_hint: Option<String>,
    pub call_site_line: Option<i64>,
}

pub fn get_kuzu_table(ntype: &str) -> Option<&'static str> {
    let t = ntype.to_uppercase();
    match t.as_str() {
        "FUNCTION" | "METHOD" => Some("Function"),
        "CLASS" => Some("Class"),
        "MODULE" | "FILE" => Some("Module"),
        "EXTERNAL" => Some("External"),
        _ => None,
    }
}

pub fn get_kuzu_rel_table(edge_type: &str) -> Option<&'static str> {
    match edge_type.to_uppercase().as_str() {
        "CALLS" => Some("Calls"),
        "IMPORTS" => Some("Imports"),
        "CONTAINS" => Some("Contains"),
        "DEFINES" => Some("Defines"),
        _ => None,
    }
}

fn init_schema(conn: &KuzuConnection<'_>) -> Result<()> {
    let _ = conn.query("CREATE NODE TABLE IF NOT EXISTS Module (name STRING, file_path STRING, PRIMARY KEY (name))");
    let _ = conn.query("CREATE NODE TABLE IF NOT EXISTS Function (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))");
    let _ = conn.query("CREATE NODE TABLE IF NOT EXISTS Class (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))");
    let _ = conn.query(
        "CREATE NODE TABLE IF NOT EXISTS External (fqn STRING, name STRING, PRIMARY KEY (fqn))",
    );

    let _ = conn.query(
        "CREATE REL TABLE IF NOT EXISTS Imports (FROM Module TO Module, FROM Module TO External)",
    );
    let _ = conn.query("CREATE REL TABLE IF NOT EXISTS Calls (FROM Function TO Function, FROM Function TO Class, FROM Class TO Function, FROM Class TO Class, FROM Function TO External, FROM Class TO External, FROM Module TO External, FROM Module TO Function, FROM Module TO Class)");
    let _ = conn.query(
        "CREATE REL TABLE IF NOT EXISTS Defines (FROM Module TO Function, FROM Module TO Class)",
    );
    let _ = conn.query(
        "CREATE REL TABLE IF NOT EXISTS Contains (FROM Class TO Function, FROM Class TO Class)",
    );
    Ok(())
}

fn clear_graph(conn: &KuzuConnection<'_>) -> Result<()> {
    let _ = conn.query("MATCH (a:Function) DETACH DELETE a");
    let _ = conn.query("MATCH (a:Class) DETACH DELETE a");
    let _ = conn.query("MATCH (a:Module) DETACH DELETE a");
    let _ = conn.query("MATCH (a:External) DETACH DELETE a");
    Ok(())
}

pub fn sync_graph_store(
    sqlite: &SqliteConnection,
    graph_path: impl AsRef<Path>,
) -> Result<GraphSyncStats> {
    let graph_path = graph_path.as_ref();
    if let Some(parent) = graph_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create graph parent {}", parent.display()))?;
    }
    let db = Database::new(graph_path, SystemConfig::default())
        .with_context(|| format!("failed to open Kuzu graph store {}", graph_path.display()))?;
    let conn = KuzuConnection::new(&db)?;
    init_schema(&conn)?;
    clear_graph(&conn)?;

    let nodes = load_nodes(sqlite)?;
    let edges = load_edges(sqlite)?;

    let mut nodes_synced = 0;
    for node in &nodes {
        if let Some(tbl) = get_kuzu_table(&node.node_type) {
            let executed = if tbl == "Module" {
                if let Ok(mut stmt) =
                    conn.prepare("MERGE (n:Module {name: $name}) SET n.file_path = $fp")
                {
                    conn.execute(
                        &mut stmt,
                        vec![
                            ("name", Value::String(node.fqn.clone())),
                            ("fp", Value::String(node.file_path.clone())),
                        ],
                    )
                    .is_ok()
                } else {
                    false
                }
            } else {
                let query = format!(
                    "MERGE (n:{} {{fqn: $fqn}}) SET n.name = $name, n.file_path = $fp",
                    tbl
                );
                if let Ok(mut stmt) = conn.prepare(&query) {
                    conn.execute(
                        &mut stmt,
                        vec![
                            ("fqn", Value::String(node.fqn.clone())),
                            ("name", Value::String(node.name.clone())),
                            ("fp", Value::String(node.file_path.clone())),
                        ],
                    )
                    .is_ok()
                } else {
                    false
                }
            };
            if executed {
                nodes_synced += 1;
            }
        }
    }

    let mut edges_synced = 0;
    for edge in &edges {
        let unresolved_target = is_unresolved_target(&edge.target_id);
        let tgt_tbl = if unresolved_target {
            Some("External")
        } else {
            get_kuzu_table(edge.target_kind_hint.as_deref().unwrap_or(""))
        };
        if tgt_tbl == Some("External") {
            if let Ok(mut stmt) = conn.prepare("MERGE (n:External {fqn: $fqn}) SET n.name = $name")
            {
                let name = edge.target_name.clone().unwrap_or_else(|| {
                    edge.target_id
                        .split("::")
                        .last()
                        .unwrap_or(&edge.target_id)
                        .to_string()
                });
                let _ = conn.execute(
                    &mut stmt,
                    vec![
                        ("fqn", Value::String(edge_target_fqn(edge))),
                        ("name", Value::String(name)),
                    ],
                );
            }
        }
    }

    let mut fqn_map = HashMap::new();
    for n in &nodes {
        fqn_map.insert(n.id.clone(), (n.fqn.clone(), get_kuzu_table(&n.node_type)));
    }

    for edge in &edges {
        let src_info = fqn_map.get(&edge.source_id);
        let tgt_info = fqn_map.get(&edge.target_id);

        let (src_fqn, src_tbl) = match src_info {
            Some(i) => (i.0.clone(), i.1),
            None => continue,
        };

        let (tgt_fqn, tgt_tbl) = match tgt_info {
            Some(i) => (i.0.clone(), i.1),
            None => {
                let t_tbl = if is_unresolved_target(&edge.target_id) {
                    Some("External")
                } else {
                    get_kuzu_table(edge.target_kind_hint.as_deref().unwrap_or(""))
                };
                (edge_target_fqn(edge), t_tbl)
            }
        };

        let rel = get_kuzu_rel_table(&edge.edge_type);

        if let (Some(s_tbl), Some(t_tbl), Some(r_tbl)) = (src_tbl, tgt_tbl, rel) {
            let query = if s_tbl == "Module" && t_tbl == "Module" {
                format!(
                    "MATCH (a:Module {{name: $s}}), (b:Module {{name: $t}}) MERGE (a)-[:{}]->(b)",
                    r_tbl
                )
            } else if s_tbl == "Module" {
                format!(
                    "MATCH (a:Module {{name: $s}}), (b:{} {{fqn: $t}}) MERGE (a)-[:{}]->(b)",
                    t_tbl, r_tbl
                )
            } else if t_tbl == "Module" {
                format!(
                    "MATCH (a:{} {{fqn: $s}}), (b:Module {{name: $t}}) MERGE (a)-[:{}]->(b)",
                    s_tbl, r_tbl
                )
            } else {
                format!(
                    "MATCH (a:{} {{fqn: $s}}), (b:{} {{fqn: $t}}) MERGE (a)-[:{}]->(b)",
                    s_tbl, t_tbl, r_tbl
                )
            };
            if let Ok(mut stmt) = conn.prepare(&query) {
                if conn
                    .execute(
                        &mut stmt,
                        vec![
                            ("s", Value::String(src_fqn.clone())),
                            ("t", Value::String(tgt_fqn.clone())),
                        ],
                    )
                    .is_ok()
                {
                    edges_synced += 1;
                }
            }
        }
    }

    Ok(GraphSyncStats {
        nodes: nodes_synced,
        edges: edges_synced,
    })
}

pub fn sync_file_graph(
    graph_path: impl AsRef<Path>,
    module_name: &str,
    rel_path: &str,
    nodes: &[cortex_parsers::NodeRecord],
    edges: &[cortex_parsers::EdgeRecord],
) -> Result<()> {
    let graph_path = graph_path.as_ref();
    if !graph_path.exists() {
        return Ok(());
    }
    let db = Database::new(graph_path, SystemConfig::default())?;
    let conn = KuzuConnection::new(&db)?;
    init_schema(&conn)?;

    if let Ok(mut stmt) = conn.prepare("MERGE (m:Module {name: $name}) SET m.file_path = $path") {
        let _ = conn.execute(
            &mut stmt,
            vec![
                ("name", Value::String(module_name.to_string())),
                ("path", Value::String(rel_path.to_string())),
            ],
        );
    }

    for node in nodes {
        if let Some(tbl) = get_kuzu_table(&node.node_type) {
            if tbl == "Function" || tbl == "Class" {
                let query = format!(
                    "MERGE (n:{} {{fqn: $fqn}}) SET n.name = $name, n.file_path = $fp",
                    tbl
                );
                if let Ok(mut stmt) = conn.prepare(&query) {
                    let _ = conn.execute(
                        &mut stmt,
                        vec![
                            ("fqn", Value::String(node.fqn.clone())),
                            ("name", Value::String(node.name.clone())),
                            ("fp", Value::String(node.file_path.clone())),
                        ],
                    );
                }

                let edge_query = format!("MATCH (m:Module {{name: $mod_name}}), (n:{} {{fqn: $fqn}}) MERGE (m)-[:Defines]->(n)", tbl);
                if let Ok(mut edge_stmt) = conn.prepare(&edge_query) {
                    let _ = conn.execute(
                        &mut edge_stmt,
                        vec![
                            ("mod_name", Value::String(module_name.to_string())),
                            ("fqn", Value::String(node.fqn.clone())),
                        ],
                    );
                }
            }
        }
    }

    for edge in edges {
        let Some(s_node) = find_node_by_id_or_fqn(nodes, &edge.source_id) else {
            continue;
        };
        let s_tbl = get_kuzu_table(&s_node.node_type).unwrap_or("Function");
        let (target_fqn, target_kind) = edge_target_for_incremental(nodes, edge);
        let t_tbl = get_kuzu_table(&target_kind).unwrap_or("Function");
        let r_tbl = get_kuzu_rel_table(&edge.edge_type).unwrap_or("Calls");

        if t_tbl == "External" {
            if let Ok(mut stmt) = conn.prepare("MERGE (n:External {fqn: $fqn}) SET n.name = $name")
            {
                let name = edge.target_name.clone().unwrap_or_else(|| {
                    target_fqn
                        .split("::")
                        .last()
                        .unwrap_or(&target_fqn)
                        .to_string()
                });
                let _ = conn.execute(
                    &mut stmt,
                    vec![
                        ("fqn", Value::String(target_fqn.clone())),
                        ("name", Value::String(name)),
                    ],
                );
            }
        }

        let edge_query = format!(
            "MATCH (a:{} {{fqn: $s}}), (b:{} {{fqn: $t}}) MERGE (a)-[:{}]->(b)",
            s_tbl, t_tbl, r_tbl
        );
        if let Ok(mut edge_stmt) = conn.prepare(&edge_query) {
            let _ = conn.execute(
                &mut edge_stmt,
                vec![
                    ("s", Value::String(s_node.fqn.clone())),
                    ("t", Value::String(target_fqn)),
                ],
            );
        }
    }

    Ok(())
}

fn is_unresolved_target(target_id: &str) -> bool {
    target_id.starts_with(UNRESOLVED_TARGET_PREFIX)
}

fn edge_target_fqn(edge: &GraphEdgeRow) -> String {
    edge.target_fqn_hint
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| edge.target_id.clone())
}

fn find_node_by_id_or_fqn<'a>(
    nodes: &'a [cortex_parsers::NodeRecord],
    id_or_fqn: &str,
) -> Option<&'a cortex_parsers::NodeRecord> {
    nodes
        .iter()
        .find(|node| node.id == id_or_fqn || node.fqn == id_or_fqn)
}

fn edge_target_for_incremental(
    nodes: &[cortex_parsers::NodeRecord],
    edge: &cortex_parsers::EdgeRecord,
) -> (String, String) {
    if let Some(target) = find_node_by_id_or_fqn(nodes, &edge.target_id) {
        return (target.fqn.clone(), target.node_type.clone());
    }

    if is_unresolved_target(&edge.target_id) {
        return (
            edge.target_fqn_hint
                .clone()
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| edge.target_id.clone()),
            "EXTERNAL".to_string(),
        );
    }

    (
        edge.target_fqn_hint
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| edge.target_id.clone()),
        edge.target_kind_hint
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "FUNCTION".to_string()),
    )
}

pub fn graph_neighbors(
    graph_path: impl AsRef<Path>,
    node_fqn: &str,
    direction: GraphDirection,
    limit: usize,
) -> Result<Vec<String>> {
    let graph_path = graph_path.as_ref();
    if !graph_path.exists() {
        return Ok(Vec::new());
    }
    let db = Database::new(graph_path, SystemConfig::default())?;
    let conn = KuzuConnection::new(&db)?;
    init_schema(&conn)?;

    let tables = ["Function", "Class", "Module", "External"];
    let mut out = Vec::new();

    if matches!(direction, GraphDirection::Callees | GraphDirection::Both) {
        for s_tbl in &tables {
            for t_tbl in &tables {
                let q = if *s_tbl == "Module" {
                    format!(
                        "MATCH (src:{})-[]->(dst:{}) WHERE src.name = $id RETURN dst.fqn",
                        s_tbl, t_tbl
                    )
                } else if *t_tbl == "Module" {
                    format!(
                        "MATCH (src:{})-[]->(dst:{}) WHERE src.fqn = $id RETURN dst.name",
                        s_tbl, t_tbl
                    )
                } else {
                    format!(
                        "MATCH (src:{})-[]->(dst:{}) WHERE src.fqn = $id RETURN dst.fqn",
                        s_tbl, t_tbl
                    )
                };
                out.extend(query_neighbor_ids(&conn, &q, node_fqn, limit)?);
            }
        }
    }

    if out.len() < limit && matches!(direction, GraphDirection::Callers | GraphDirection::Both) {
        let rem = limit - out.len();
        for s_tbl in &tables {
            for t_tbl in &tables {
                let q = if *t_tbl == "Module" {
                    format!(
                        "MATCH (src:{})-[]->(dst:{}) WHERE dst.name = $id RETURN src.fqn",
                        s_tbl, t_tbl
                    )
                } else if *s_tbl == "Module" {
                    format!(
                        "MATCH (src:{})-[]->(dst:{}) WHERE dst.fqn = $id RETURN src.name",
                        s_tbl, t_tbl
                    )
                } else {
                    format!(
                        "MATCH (src:{})-[]->(dst:{}) WHERE dst.fqn = $id RETURN src.fqn",
                        s_tbl, t_tbl
                    )
                };
                out.extend(query_neighbor_ids(&conn, &q, node_fqn, rem)?);
            }
        }
    }

    out.sort();
    out.dedup();
    out.truncate(limit);
    Ok(out)
}

fn load_nodes(sqlite: &SqliteConnection) -> Result<Vec<GraphNodeRow>> {
    let mut stmt = sqlite.prepare(
        "SELECT id, fqn, name, type, file_path, start_line, language
         FROM nodes
         WHERE category = 'SOURCE'
         ORDER BY id",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(GraphNodeRow {
            id: row.get(0)?,
            fqn: row.get(1)?,
            name: row.get(2)?,
            node_type: row.get(3)?,
            file_path: row.get(4)?,
            start_line: row.get(5)?,
            language: row.get(6)?,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

fn load_edges(sqlite: &SqliteConnection) -> Result<Vec<GraphEdgeRow>> {
    let mut stmt = sqlite.prepare(
        "SELECT e.source_id, e.target_id, e.type, e.target_name, e.target_kind_hint, e.target_fqn_hint, e.call_site_line
         FROM edges e
         JOIN nodes src ON src.id = e.source_id
         LEFT JOIN nodes dst ON dst.id = e.target_id
         WHERE e.resolution_status = 'resolved' OR e.resolution_status = 'unresolved'
         ORDER BY e.source_id, e.target_id, e.type",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(GraphEdgeRow {
            source_id: row.get(0)?,
            target_id: row.get(1)?,
            edge_type: row.get(2)?,
            target_name: row.get(3)?,
            target_kind_hint: row.get(4)?,
            target_fqn_hint: row.get(5)?,
            call_site_line: row.get(6)?,
        })
    })?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row?);
    }
    Ok(out)
}

fn query_neighbor_ids(
    conn: &KuzuConnection<'_>,
    query: &str,
    node_id: &str,
    limit: usize,
) -> Result<Vec<String>> {
    if limit == 0 {
        return Ok(Vec::new());
    }
    if let Ok(mut stmt) = conn.prepare(query) {
        if let Ok(mut result) =
            conn.execute(&mut stmt, vec![("id", Value::String(node_id.to_string()))])
        {
            let mut out = Vec::new();
            while let Some(row) = result.next() {
                if let Some(Value::String(id)) = row.into_iter().next() {
                    out.push(id);
                    if out.len() >= limit {
                        break;
                    }
                }
            }
            return Ok(out);
        }
    }
    Ok(Vec::new())
}

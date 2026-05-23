use anyhow::{Context, Result};
use rayon::prelude::*;
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};

use rusqlite::OptionalExtension;

use crate::common::{
    blake2b16_hex, category_for, file_extension, module_name_for, now_unix_seconds,
    read_text_source, vector_prefix_for_path, workspace_db_path, workspace_id_for,
};

#[derive(Default, Serialize)]
struct IndexStats {
    total_files: usize,
    indexed: usize,
    skipped: usize,
    errors: usize,
    deleted: usize,
}

#[derive(Serialize)]
struct IndexReport {
    total_files: usize,
    indexed: usize,
    skipped: usize,
    errors: usize,
    deleted: usize,
    vector_items_by_prefix: BTreeMap<String, Vec<VectorItem>>,
}

#[derive(Serialize, Clone)]
pub(crate) struct VectorItem {
    id: String,
    text: String,
    meta: VectorMeta,
}

#[derive(Serialize, Clone)]
pub(crate) struct VectorMeta {
    module: String,
    file: String,
    #[serde(rename = "type")]
    node_type: String,
    category: String,
}

pub(crate) struct ProcessResult {
    pub(crate) outcome: ProcessOutcome,
}

pub(crate) enum ProcessOutcome {
    RustIndexed,
    Skipped,
    Deleted,
}

pub(crate) fn cmd_index(workspace: &Path, force: bool) -> Result<()> {
    let workspace = workspace.to_path_buf();
    let settings = cortex_scanner::load_settings(&workspace).unwrap_or_default();
    let files = cortex_scanner::scan_files(&workspace, None)?;
    let db_path = workspace_db_path(&workspace);
    let mut conn = cortex_storage::open_connection(&db_path)
        .with_context(|| format!("failed to open workspace db: {}", db_path.display()))?;
    cortex_storage::init_schema(&conn)?;

    let current_files: BTreeSet<String> = files.iter().cloned().collect();
    let deleted_count = cleanup_deleted_file_records(&mut conn, &current_files)?;
    let cache_map = load_file_cache_hash_map(&conn)?;
    drop(conn);

    let mut stats = IndexStats::default();
    stats.total_files = files.len();
    stats.deleted = deleted_count;

    let mut vector_items_by_prefix: BTreeMap<String, Vec<VectorItem>> = BTreeMap::new();

    let prepared = files
        .par_iter()
        .map(|rel_path| {
            inspect_path(
                &workspace,
                &settings,
                rel_path,
                force,
                cache_map.get(rel_path).map(|s| s.as_str()),
                true,
                None,
            )
        })
        .collect::<Vec<_>>();

    let mut conn = cortex_storage::open_connection(&db_path)
        .with_context(|| format!("failed to reopen workspace db: {}", db_path.display()))?;
    cortex_storage::init_schema(&conn)?;

    for item in prepared {
        match item {
            Ok(InspectOutcome::Indexed(indexed)) => {
                write_indexed_path(&mut conn, &indexed)?;
                stats.indexed += 1;
                let prefix = vector_prefix_for_path(&indexed.rel_path);
                vector_items_by_prefix
                    .entry(prefix)
                    .or_default()
                    .extend(indexed.vector_items);
            }
            Ok(InspectOutcome::Skipped) => {
                stats.skipped += 1;
            }
            Ok(InspectOutcome::Deleted(rel_path)) => {
                stats.deleted += 1;
                cleanup_file_records(&mut conn, &rel_path)?;
            }
            Err(err) => {
                stats.errors += 1;
                tracing::warn!(error = %err, "indexing failed");
            }
        }
    }

    cortex_storage::resolve_unresolved_edges(&mut conn)?;

    let report = IndexReport {
        total_files: stats.total_files,
        indexed: stats.indexed,
        skipped: stats.skipped,
        errors: stats.errors,
        deleted: stats.deleted,
        vector_items_by_prefix,
    };
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}

pub(crate) fn cmd_index_file(
    workspace: &Path,
    rel_path: &Path,
    source_path: Option<&Path>,
    force: bool,
) -> Result<()> {
    let workspace = workspace.to_path_buf();
    let settings = cortex_scanner::load_settings(&workspace).unwrap_or_default();
    let rel_path = rel_path.to_string_lossy().replace('\\', "/");
    let db_path = workspace_db_path(&workspace);
    let mut conn = cortex_storage::open_connection(&db_path)
        .with_context(|| format!("failed to open workspace db: {}", db_path.display()))?;
    cortex_storage::init_schema(&conn)?;

    let cached_hash = load_file_cache_hash(&conn, &rel_path)?;
    let source_path = source_path
        .map(PathBuf::from)
        .unwrap_or_else(|| workspace.join(&rel_path));
    let existed_before = file_record_exists(&conn, &rel_path)?;

    let outcome = if !source_path.exists() {
        cleanup_file_records(&mut conn, &rel_path)?;
        ProcessOutcome::Deleted
    } else {
        process_path(
            &workspace,
            &settings,
            &mut conn,
            &rel_path,
            force,
            cached_hash.as_deref(),
            false,
            Some(&source_path),
        )?
        .outcome
    };

    cortex_storage::resolve_unresolved_edges(&mut conn)?;

    let report = match outcome {
        ProcessOutcome::RustIndexed => serde_json::json!({
            "status": if existed_before { "updated" } else { "created" },
            "reason": "indexed"
        }),
        ProcessOutcome::Skipped => serde_json::json!({
            "status": "skipped",
            "reason": "hash unchanged",
            "chunks": 0
        }),
        ProcessOutcome::Deleted => serde_json::json!({
            "status": "deleted",
            "reason": "File removed from DB"
        }),
    };
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}

enum InspectOutcome {
    Indexed(PreparedIndex),
    Skipped,
    Deleted(String),
}

struct PreparedIndex {
    rel_path: String,
    file_hash: String,
    module_name: String,
    workspace_id: String,
    category: &'static str,
    nodes: Vec<cortex_parsers::NodeRecord>,
    edges: Vec<cortex_parsers::EdgeRecord>,
    vector_items: Vec<VectorItem>,
}

pub(crate) fn parse_indexable_file(rel_path: &str, file: &Path) -> Result<cortex_parsers::ParseResult> {
    let ext = file_extension(rel_path);
    let result = match ext.as_str() {
        "pdf" => cortex_parsers::parse_pdf_file(rel_path, file),
        _ => {
            let source = read_text_source(file)?;
            match ext.as_str() {
                "md" => cortex_parsers::parse_markdown_file(rel_path, &source),
                "html" => cortex_parsers::parse_html_file(rel_path, &source),
                "css" => cortex_parsers::parse_css_file(rel_path, &source),
                "py" => cortex_parsers::parse_python_file(rel_path, &source),
                "java" => cortex_parsers::parse_java_file(rel_path, &source),
                "c" | "cpp" | "h" | "hpp" | "cc" | "cxx" => {
                    cortex_parsers::parse_c_file(rel_path, &source)
                }
                "cs" => cortex_parsers::parse_csharp_file(rel_path, &source),
                "ts" => cortex_parsers::parse_ts_file(rel_path, &source, "typescript"),
                "tsx" => cortex_parsers::parse_ts_file(rel_path, &source, "tsx"),
                other => anyhow::bail!("unsupported extension for parse-file: .{}", other),
            }
        }
    };
    Ok(result)
}

pub(crate) fn process_path(
    workspace: &Path,
    settings: &serde_yaml::Value,
    conn: &mut rusqlite::Connection,
    rel_path: &str,
    force: bool,
    cached_hash: Option<&str>,
    include_vector_items: bool,
    source_path: Option<&Path>,
) -> Result<ProcessResult> {
    let indexed = inspect_path(
        workspace,
        settings,
        rel_path,
        force,
        cached_hash,
        include_vector_items,
        source_path,
    )?;
    let vector_items = match indexed {
        InspectOutcome::Indexed(indexed) => {
            write_indexed_path(conn, &indexed)?;
            indexed.vector_items
        }
        InspectOutcome::Skipped => {
            return Ok(ProcessResult {
                outcome: ProcessOutcome::Skipped,
            });
        }
        InspectOutcome::Deleted(rel_path) => {
            cleanup_file_records(conn, &rel_path)?;
            return Ok(ProcessResult {
                outcome: ProcessOutcome::Deleted,
            });
        }
    };
    let _ = vector_items;
    Ok(ProcessResult {
        outcome: ProcessOutcome::RustIndexed,
    })
}

fn inspect_path(
    workspace: &Path,
    settings: &serde_yaml::Value,
    rel_path: &str,
    force: bool,
    cached_hash: Option<&str>,
    include_vector_items: bool,
    source_path: Option<&Path>,
) -> Result<InspectOutcome> {
    let file_path = source_path
        .map(PathBuf::from)
        .unwrap_or_else(|| workspace.join(rel_path));
    let ext = file_extension(rel_path);

    if !file_path.exists() {
        return Ok(InspectOutcome::Deleted(rel_path.to_string()));
    }

    let file_hash = if ext == "pdf" {
        blake2b16_hex(&std::fs::read(&file_path)?)
    } else {
        blake2b16_hex(read_text_source(&file_path)?.as_bytes())
    };

    if !force && cached_hash.is_some_and(|cached| cached == file_hash) {
        return Ok(InspectOutcome::Skipped);
    }

    let result = parse_indexable_file(rel_path, &file_path)?;
    let workspace_id = workspace_id_for(workspace);
    let module_name = module_name_for(rel_path, settings);
    let category = category_for(rel_path);

    let vector_items = if include_vector_items {
        let clean_source = if rel_path.starts_with(".cortex/") {
            strip_frontmatter(&read_text_source(&file_path)?)
        } else {
            read_text_source(&file_path)?
        };
        build_vector_items(rel_path, &module_name, &clean_source, &result.nodes)
    } else {
        Vec::new()
    };

    Ok(InspectOutcome::Indexed(PreparedIndex {
        rel_path: rel_path.to_string(),
        file_hash,
        module_name,
        workspace_id,
        category,
        nodes: result.nodes,
        edges: result.edges,
        vector_items,
    }))
}

fn write_indexed_path(
    conn: &mut rusqlite::Connection,
    indexed: &PreparedIndex,
) -> Result<()> {
    let batch = cortex_storage::FileWriteBatch {
        file_path: &indexed.rel_path,
        file_hash: &indexed.file_hash,
        indexed_at: now_unix_seconds(),
        module: Some(indexed.module_name.as_str()),
        workspace_id: Some(indexed.workspace_id.as_str()),
        category: Some(indexed.category),
        nodes: &indexed.nodes,
        edges: &indexed.edges,
    };

    Ok(cortex_storage::write_file_batch(conn, &batch)?)
}

fn build_vector_items(
    rel_path: &str,
    module_name: &str,
    clean_source: &str,
    nodes: &[cortex_parsers::NodeRecord],
) -> Vec<VectorItem> {
    let category = category_for(rel_path).to_string();
    let clean_text = clean_source.chars().take(1200).collect::<String>();
    let mut items = Vec::with_capacity(nodes.len());

    for node in nodes {
        let mut text = format!("{} {}\n", node.node_type, node.fqn);
        if let Some(signature) = &node.signature {
            text.push_str(&format!("Sig: {}\n", signature));
        }
        if category == "RULE" {
            text.push_str(&clean_text);
        } else {
            text.push_str(&node.raw_body.chars().take(1200).collect::<String>());
        }

        items.push(VectorItem {
            id: node.id.clone(),
            text,
            meta: VectorMeta {
                module: module_name.to_string(),
                file: rel_path.to_string(),
                node_type: node.node_type.clone(),
                category: category.clone(),
            },
        });
    }

    items
}

fn load_file_cache_hash_map(conn: &rusqlite::Connection) -> Result<HashMap<String, String>> {
    let mut stmt = conn.prepare("SELECT file_path, hash FROM file_cache")?;
    let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
    let mut out = HashMap::new();
    for row in rows {
        let (path, hash) = row?;
        out.insert(path, hash);
    }
    Ok(out)
}

fn load_file_cache_hash(conn: &rusqlite::Connection, rel_path: &str) -> Result<Option<String>> {
    let mut stmt = conn.prepare("SELECT hash FROM file_cache WHERE file_path = ?1")?;
    let hash = stmt
        .query_row([rel_path], |row| row.get::<_, String>(0))
        .optional()?;
    Ok(hash)
}

fn file_record_exists(conn: &rusqlite::Connection, rel_path: &str) -> Result<bool> {
    let mut stmt = conn.prepare("SELECT 1 FROM nodes WHERE file_path = ?1 LIMIT 1")?;
    let exists = stmt
        .query_row([rel_path], |_| Ok(()))
        .optional()?
        .is_some();
    Ok(exists)
}

fn cleanup_deleted_file_records(
    conn: &mut rusqlite::Connection,
    current_files: &BTreeSet<String>,
) -> Result<usize> {
    let cached_paths = {
        let mut stmt = conn.prepare("SELECT file_path FROM file_cache")?;
        let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
        let mut cached = Vec::new();
        for row in rows {
            cached.push(row?);
        }
        cached
    };
    let mut deleted = 0usize;
    for path in cached_paths {
        if !current_files.contains(&path) {
            cleanup_file_records(conn, &path)?;
            deleted += 1;
        }
    }
    Ok(deleted)
}

fn cleanup_file_records(conn: &mut rusqlite::Connection, rel_path: &str) -> Result<()> {
    let tx = conn.transaction()?;
    tx.execute(
        "DELETE FROM edges WHERE source_id IN (SELECT id FROM nodes WHERE file_path = ?1) \
         OR target_id IN (SELECT id FROM nodes WHERE file_path = ?1)",
        [rel_path],
    )?;
    tx.execute("DELETE FROM nodes WHERE file_path = ?1", [rel_path])?;
    tx.execute("DELETE FROM file_cache WHERE file_path = ?1", [rel_path])?;
    tx.commit()?;
    Ok(())
}

fn strip_frontmatter(content: &str) -> String {
    if let Some(rest) = content.strip_prefix("---\n") {
        if let Some(end) = rest.find("\n---\n") {
            return rest[end + 5..].to_string();
        }
    }
    content.to_string()
}

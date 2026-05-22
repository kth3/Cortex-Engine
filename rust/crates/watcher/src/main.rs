//! cortex-watcher — Cortex 파일 감시 데몬 진입점.
//!
//! Python `cortex/watch/daemon.py` 대응.
//! notify 기반 파일 감시 → scanner → parser → SQLite writer.

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use notify::{Config, Event, RecommendedWatcher, RecursiveMode, Watcher};
use sha1::Digest as _;
#[cfg(windows)]
use signal_hook::consts::signal::SIGBREAK;
use signal_hook::consts::signal::{SIGINT, SIGTERM};
use signal_hook::flag as signal_flag;
use std::collections::BTreeSet;
use std::env;
use std::path::{Path, PathBuf};
use std::process::Command as ProcessCommand;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc::{self, RecvTimeoutError},
    Arc,
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const WATCH_DEBOUNCE: Duration = Duration::from_secs(5);
const WATCH_HEARTBEAT: Duration = Duration::from_secs(60);
const SQLITE_DB_FILENAME: &str = "memories.db";
const WORKSPACES_DIRNAME: &str = "workspaces";

#[derive(Parser, Debug)]
#[command(
    name = "cortex-watcher",
    version,
    about = "Cortex Rust 파일 감시·인덱싱 데몬"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// 워크스페이스 스캔 후 인덱싱 대상 파일 목록 출력 (JSON).
    Scan {
        /// 스캔 대상 워크스페이스 경로
        #[arg(short, long)]
        workspace: PathBuf,
        /// 결과 포맷: json(기본) 또는 lines
        #[arg(long, default_value = "json")]
        format: String,
    },
    /// 단일 파일을 파싱하여 `{"nodes": [...], "edges": [...]}` JSON 출력.
    ParseFile {
        /// 상대 또는 절대 파일 경로
        #[arg(short, long)]
        file: PathBuf,
        /// nodes/edges JSON 출력 시 사용할 file_path (db_path). 미지정 시 입력 경로.
        #[arg(long)]
        rel: Option<String>,
    },
    /// 감시 데몬 모드.
    Watch {
        #[arg(short, long)]
        workspace: PathBuf,
    },
}

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let cli = Cli::parse();

    match cli.command {
        Command::Scan { workspace, format } => cmd_scan(&workspace, &format),
        Command::ParseFile { file, rel } => cmd_parse_file(&file, rel.as_deref()),
        Command::Watch { workspace } => cmd_watch(&workspace),
    }
}

fn cmd_parse_file(file: &Path, rel: Option<&str>) -> Result<()> {
    let rel_path = rel
        .map(|s| s.to_string())
        .unwrap_or_else(|| file.to_string_lossy().replace('\\', "/"));
    let result = parse_indexable_file(&rel_path, file)?;
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

fn cmd_scan(workspace: &Path, format: &str) -> Result<()> {
    let files = cortex_scanner::scan_files(workspace, None)?;
    match format {
        "lines" => {
            for f in &files {
                println!("{}", f);
            }
        }
        _ => {
            println!("{}", serde_json::to_string(&files)?);
        }
    }
    Ok(())
}

fn cmd_watch(workspace: &Path) -> Result<()> {
    let workspace = workspace
        .canonicalize()
        .unwrap_or_else(|_| workspace.to_path_buf());
    let settings = cortex_scanner::load_settings(&workspace).unwrap_or_default();
    let ignore_patterns = load_ignore_patterns(&workspace, &settings);
    let db_path = workspace_db_path(&workspace);
    let mut conn = cortex_storage::open_connection(&db_path)
        .with_context(|| format!("failed to open workspace db: {}", db_path.display()))?;

    let shutdown = Arc::new(AtomicBool::new(false));
    install_signal_handlers(Arc::clone(&shutdown))?;

    let (tx, rx) = mpsc::channel();
    let mut watcher = RecommendedWatcher::new(
        move |res| {
            let _ = tx.send(res);
        },
        Config::default(),
    )?;
    watcher.watch(&workspace, RecursiveMode::Recursive)?;

    tracing::info!(workspace = %workspace.display(), db = %db_path.display(), "watch mode started");

    let mut pending = BTreeSet::<String>::new();
    let mut last_event_at: Option<Instant> = None;
    let mut last_heartbeat = Instant::now();

    loop {
        if shutdown.load(Ordering::SeqCst) {
            break;
        }

        match rx.recv_timeout(Duration::from_millis(250)) {
            Ok(Ok(event)) => {
                enqueue_event_paths(
                    &workspace,
                    &settings,
                    &ignore_patterns,
                    &event,
                    &mut pending,
                    &mut last_event_at,
                );
            }
            Ok(Err(err)) => tracing::warn!(error = %err, "watch event error"),
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => break,
        }

        if last_event_at
            .as_ref()
            .map(|stamp| stamp.elapsed() >= WATCH_DEBOUNCE)
            .unwrap_or(false)
            && !pending.is_empty()
        {
            flush_pending_batch(&workspace, &settings, &mut conn, &mut pending)?;
            last_event_at = None;
        }

        if last_heartbeat.elapsed() >= WATCH_HEARTBEAT {
            tracing::info!(pending = pending.len(), "watcher alive");
            last_heartbeat = Instant::now();
        }
    }

    if !pending.is_empty() {
        flush_pending_batch(&workspace, &settings, &mut conn, &mut pending)?;
    }

    tracing::info!("watcher shutdown complete");
    Ok(())
}

fn enqueue_event_paths(
    workspace: &Path,
    settings: &serde_yaml::Value,
    ignore_patterns: &[String],
    event: &Event,
    pending: &mut BTreeSet<String>,
    last_event_at: &mut Option<Instant>,
) {
    for path in &event.paths {
        let Some(rel_path) = workspace_relative(workspace, path) else {
            continue;
        };
        if !should_track_path(&rel_path, settings, ignore_patterns) {
            continue;
        }
        pending.insert(rel_path);
        *last_event_at = Some(Instant::now());
    }
}

fn flush_pending_batch(
    workspace: &Path,
    settings: &serde_yaml::Value,
    conn: &mut rusqlite::Connection,
    pending: &mut BTreeSet<String>,
) -> Result<()> {
    let files: Vec<String> = pending.iter().cloned().collect();
    pending.clear();

    let mut rust_count = 0usize;
    let mut python_count = 0usize;
    let mut deleted_count = 0usize;

    for rel_path in files {
        match process_path(workspace, settings, conn, &rel_path) {
            Ok(ProcessOutcome::RustIndexed) => rust_count += 1,
            Ok(ProcessOutcome::PythonIndexed) => python_count += 1,
            Ok(ProcessOutcome::Deleted) => deleted_count += 1,
            Err(err) => {
                tracing::error!(file = %rel_path, error = %err, "failed to process watch event")
            }
        }
    }

    tracing::info!(
        rust_indexed = rust_count,
        python_indexed = python_count,
        deleted = deleted_count,
        "debounce batch complete"
    );

    Ok(())
}

enum ProcessOutcome {
    RustIndexed,
    PythonIndexed,
    Deleted,
}

fn process_path(
    workspace: &Path,
    settings: &serde_yaml::Value,
    conn: &mut rusqlite::Connection,
    rel_path: &str,
) -> Result<ProcessOutcome> {
    let file_path = workspace.join(rel_path);
    let ext = file_extension(rel_path);

    if ext == "py" {
        run_python_indexer(workspace, rel_path)?;
        return Ok(ProcessOutcome::PythonIndexed);
    }

    if !file_path.exists() {
        cleanup_file_records(conn, rel_path)?;
        return Ok(ProcessOutcome::Deleted);
    }

    let result = parse_indexable_file(rel_path, &file_path)?;
    let workspace_id = workspace_id_for(workspace);
    let module_name = module_name_for(rel_path, settings);
    let category = category_for(rel_path);

    let file_hash = if ext == "pdf" {
        blake2b16_hex(&std::fs::read(&file_path)?)
    } else {
        blake2b16_hex(read_text_source(&file_path)?.as_bytes())
    };

    let batch = cortex_storage::FileWriteBatch {
        file_path: rel_path,
        file_hash: &file_hash,
        indexed_at: now_unix_seconds(),
        module: Some(module_name.as_str()),
        workspace_id: Some(workspace_id.as_str()),
        category: Some(category),
        nodes: &result.nodes,
        edges: &result.edges,
    };

    cortex_storage::write_file_batch(conn, &batch)?;
    Ok(ProcessOutcome::RustIndexed)
}

fn parse_indexable_file(rel_path: &str, file: &Path) -> Result<cortex_parsers::ParseResult> {
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

fn read_text_source(file: &Path) -> Result<String> {
    let raw = std::fs::read_to_string(file)
        .with_context(|| format!("failed to read {}", file.display()))?;
    Ok(raw.replace("\r\n", "\n"))
}

fn run_python_indexer(workspace: &Path, rel_path: &str) -> Result<()> {
    let python = env::var("CORTEX_PYTHON_EXECUTABLE").unwrap_or_else(|_| "python".to_string());
    let output = ProcessCommand::new(python)
        .args(["-m", "cortex.indexing.cli"])
        .arg(workspace)
        .arg("--file")
        .arg(rel_path)
        .output()
        .with_context(|| format!("failed to run python indexer for {}", rel_path))?;

    if output.status.success() {
        return Ok(());
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    anyhow::bail!("python indexer failed for {}: {}", rel_path, stderr.trim());
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

fn load_ignore_patterns(workspace: &Path, settings: &serde_yaml::Value) -> Vec<String> {
    let mut patterns = cortex_scanner::load_gitignore(workspace);
    if let Some(extras) = settings
        .get("indexing_rules")
        .and_then(|rules| rules.get("exclude_paths"))
        .and_then(|value| value.as_sequence())
    {
        for value in extras {
            if let Some(pattern) = value.as_str() {
                let trimmed = pattern.trim_matches('/').to_string();
                if !trimmed.is_empty() {
                    patterns.push(trimmed);
                }
            }
        }
    }
    patterns
}

fn should_track_path(
    rel_path: &str,
    settings: &serde_yaml::Value,
    ignore_patterns: &[String],
) -> bool {
    let ext = format!(".{}", file_extension(rel_path));
    cortex_scanner::is_supported_extension(&ext)
        && cortex_scanner::should_include(rel_path, settings)
        && !cortex_scanner::should_ignore(rel_path, ignore_patterns)
}

fn file_extension(path: &str) -> String {
    Path::new(path)
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.to_lowercase())
        .unwrap_or_default()
}

fn workspace_relative(workspace: &Path, path: &Path) -> Option<String> {
    let absolute = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    absolute
        .strip_prefix(workspace)
        .ok()
        .map(|rel| rel.to_string_lossy().replace('\\', "/"))
}

fn workspace_key(workspace: &Path) -> String {
    if let Ok(key) = env::var("CORTEX_WORKSPACE_KEY") {
        return key;
    }

    let text = workspace.to_string_lossy();
    let digest = sha1::Sha1::digest(text.as_bytes());
    let hex = format!("{:x}", digest);
    hex[..12].to_string()
}

fn workspace_id_for(workspace: &Path) -> String {
    let text = workspace.to_string_lossy();
    let digest = md5::compute(text.as_bytes());
    let hex = format!("{:x}", digest);
    hex[..8].to_string()
}

fn workspace_data_home() -> PathBuf {
    if let Some(path) = env::var_os("CORTEX_DATA_HOME") {
        return PathBuf::from(path);
    }

    home_dir().join(".cortex")
}

fn workspace_db_path(workspace: &Path) -> PathBuf {
    workspace_data_dir(workspace).join(SQLITE_DB_FILENAME)
}

fn workspace_data_dir(workspace: &Path) -> PathBuf {
    let dir = workspace_data_home()
        .join(WORKSPACES_DIRNAME)
        .join(workspace_key(workspace));
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn home_dir() -> PathBuf {
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn category_for(rel_path: &str) -> &'static str {
    if rel_path.contains("skills/") {
        "SKILL"
    } else if rel_path.starts_with(".cortex/") {
        "RULE"
    } else {
        "SOURCE"
    }
}

fn module_name_for(rel_path: &str, settings: &serde_yaml::Value) -> String {
    let norm_path = rel_path.replace('\\', "/");
    let modules = settings
        .get("indexing_rules")
        .and_then(|rules| rules.get("modules"))
        .and_then(|value| value.as_mapping());

    if let Some(modules) = modules {
        for (module_name, module_paths) in modules {
            let Some(module_name) = module_name.as_str() else {
                continue;
            };
            let Some(paths) = module_paths.as_sequence() else {
                continue;
            };

            for path in paths {
                let Some(path) = path.as_str() else {
                    continue;
                };
                let normalized = path.replace('\\', "/").trim_matches('/').to_string();
                if normalized.is_empty() {
                    continue;
                }
                if norm_path.starts_with(&format!("{}/", normalized))
                    || norm_path.ends_with(&normalized)
                {
                    return module_name.to_string();
                }
            }
        }
    }

    let parts: Vec<&str> = norm_path.split('/').collect();
    if parts.len() > 1 {
        parts[0].to_string()
    } else {
        "root".to_string()
    }
}

fn blake2b16_hex(bytes: &[u8]) -> String {
    use blake2::{
        digest::{Update, VariableOutput},
        Blake2bVar,
    };

    let mut hasher = Blake2bVar::new(16).expect("blake2b output size");
    hasher.update(bytes);
    let mut output = [0u8; 16];
    hasher
        .finalize_variable(&mut output)
        .expect("blake2b finalize");
    output.iter().map(|byte| format!("{:02x}", byte)).collect()
}

fn now_unix_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or_default()
}

fn install_signal_handlers(shutdown: Arc<AtomicBool>) -> Result<()> {
    signal_flag::register(SIGINT, Arc::clone(&shutdown))?;
    signal_flag::register(SIGTERM, Arc::clone(&shutdown))?;
    #[cfg(windows)]
    {
        signal_flag::register(SIGBREAK, shutdown)?;
    }

    Ok(())
}

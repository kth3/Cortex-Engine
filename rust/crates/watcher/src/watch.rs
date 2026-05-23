use anyhow::{Context, Result};
use notify::{Config, Event, RecommendedWatcher, RecursiveMode, Watcher};
use std::collections::BTreeSet;
use std::path::Path;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc::{self, RecvTimeoutError},
    Arc,
};
use std::time::{Duration, Instant};

use crate::common::{load_ignore_patterns, should_track_path, workspace_db_path, workspace_relative};
use crate::index::{process_path, ProcessOutcome, ProcessResult};

const WATCH_DEBOUNCE: Duration = Duration::from_secs(5);
const WATCH_HEARTBEAT: Duration = Duration::from_secs(60);

pub(crate) fn cmd_watch(workspace: &Path) -> Result<()> {
    let workspace = workspace
        .canonicalize()
        .unwrap_or_else(|_| workspace.to_path_buf());
    let settings = cortex_scanner::load_settings(&workspace).unwrap_or_default();
    let ignore_patterns = load_ignore_patterns(&workspace, &settings);
    let db_path = workspace_db_path(&workspace);
    let mut conn = cortex_storage::open_connection(&db_path)
        .with_context(|| format!("failed to open workspace db: {}", db_path.display()))?;
    cortex_storage::init_schema(&conn)?;

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
    let cache_map = load_file_cache_hash_map(conn)?;

    let mut rust_count = 0usize;
    let mut python_count = 0usize;
    let mut skipped_count = 0usize;
    let mut deleted_count = 0usize;

    for rel_path in files {
        match process_path(
            workspace,
            settings,
            conn,
            &rel_path,
            false,
            cache_map.get(&rel_path).map(|s| s.as_str()),
        ) {
            Ok(ProcessResult {
                outcome: ProcessOutcome::RustIndexed,
                ..
            }) => rust_count += 1,
            Ok(ProcessResult {
                outcome: ProcessOutcome::PythonIndexed,
                ..
            }) => python_count += 1,
            Ok(ProcessResult {
                outcome: ProcessOutcome::Skipped,
                ..
            }) => skipped_count += 1,
            Ok(ProcessResult {
                outcome: ProcessOutcome::Deleted,
                ..
            }) => deleted_count += 1,
            Err(err) => {
                tracing::error!(file = %rel_path, error = %err, "failed to process watch event")
            }
        }
    }

    cortex_storage::resolve_unresolved_edges(conn)?;

    tracing::info!(
        rust_indexed = rust_count,
        python_indexed = python_count,
        skipped = skipped_count,
        deleted = deleted_count,
        "debounce batch complete"
    );

    Ok(())
}

fn load_file_cache_hash_map(conn: &rusqlite::Connection) -> Result<std::collections::HashMap<String, String>> {
    let mut stmt = conn.prepare("SELECT file_path, hash FROM file_cache")?;
    let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
    let mut out = std::collections::HashMap::new();
    for row in rows {
        let (path, hash) = row?;
        out.insert(path, hash);
    }
    Ok(out)
}

fn install_signal_handlers(shutdown: Arc<AtomicBool>) -> Result<()> {
    signal_hook::flag::register(signal_hook::consts::signal::SIGINT, Arc::clone(&shutdown))?;
    signal_hook::flag::register(signal_hook::consts::signal::SIGTERM, Arc::clone(&shutdown))?;
    #[cfg(windows)]
    {
        signal_hook::flag::register(signal_hook::consts::signal::SIGBREAK, shutdown)?;
    }

    Ok(())
}

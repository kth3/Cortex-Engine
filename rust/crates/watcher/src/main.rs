//! cortex-watcher — Cortex 파일 감시 데몬 진입점.
//!
//! Python `cortex/watch/daemon.py` 대응.
//! notify 기반 파일 감시 → scanner → parser → SQLite writer.

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "cortex-watcher", version, about = "Cortex Rust 파일 감시·인덱싱 데몬")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// 워크스페이스 스캔 후 인덱싱 대상 파일 목록 출력 (JSON).
    /// Python `cortex.scanner.scan_files()` 동등 검증용.
    Scan {
        /// 스캔 대상 워크스페이스 경로
        #[arg(short, long)]
        workspace: PathBuf,
        /// 결과 포맷: json(기본) 또는 lines
        #[arg(long, default_value = "json")]
        format: String,
    },
    /// 단일 파일을 파싱하여 `{"nodes": [...], "edges": [...]}` JSON 출력.
    /// Python `cortex.parsers.<ext>` 와 diff 비교용.
    ParseFile {
        /// 상대 또는 절대 파일 경로
        #[arg(short, long)]
        file: PathBuf,
        /// nodes/edges JSON 출력 시 사용할 file_path (db_path). 미지정 시 입력 경로.
        #[arg(long)]
        rel: Option<String>,
    },
    /// 감시 데몬 모드 (구현 예정 — Phase 4)
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
        Command::Watch { workspace } => {
            tracing::info!(?workspace, "watch mode not yet implemented (Phase 4)");
            Ok(())
        }
    }
}

fn cmd_parse_file(file: &std::path::Path, rel: Option<&str>) -> Result<()> {
    // Python `open(..., encoding="utf-8")` 기본 텍스트 모드는 \r\n→\n 정규화.
    // Rust read_to_string은 raw — 동일 출력 위해 명시적으로 정규화.
    let raw = std::fs::read_to_string(file)?;
    let source = raw.replace("\r\n", "\n");
    let rel_path = rel
        .map(|s| s.to_string())
        .unwrap_or_else(|| file.to_string_lossy().replace('\\', "/"));

    let ext = file
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.to_lowercase())
        .unwrap_or_default();

    let result = match ext.as_str() {
        "md" => cortex_parsers::parse_markdown_file(&rel_path, &source),
        "html" => cortex_parsers::parse_html_file(&rel_path, &source),
        "css" => cortex_parsers::parse_css_file(&rel_path, &source),
        other => {
            anyhow::bail!("unsupported extension for parse-file (Phase 2d only): .{}", other);
        }
    };

    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

fn cmd_scan(workspace: &std::path::Path, format: &str) -> Result<()> {
    let files = cortex_scanner::scan_files(workspace, None)?;
    match format {
        "lines" => {
            for f in &files {
                println!("{}", f);
            }
        }
        _ => {
            // 기본: JSON 배열 (Python 출력과 직접 비교 가능)
            println!("{}", serde_json::to_string(&files)?);
        }
    }
    Ok(())
}

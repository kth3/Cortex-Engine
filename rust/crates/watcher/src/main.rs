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
        Command::Watch { workspace } => {
            tracing::info!(?workspace, "watch mode not yet implemented (Phase 4)");
            Ok(())
        }
    }
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

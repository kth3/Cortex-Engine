use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::{Path, PathBuf};

use crate::index::{cmd_index, cmd_index_file, parse_indexable_file};
use crate::watch::cmd_watch;

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
    /// 워크스페이스 전체를 인덱싱하고 JSON 요약 출력.
    Index {
        #[arg(short, long)]
        workspace: PathBuf,
        #[arg(long, default_value_t = false)]
        force: bool,
    },
    /// 단일 파일을 인덱싱하고 JSON 요약 출력.
    IndexFile {
        #[arg(short, long)]
        workspace: PathBuf,
        /// 워크스페이스 기준 상대 경로 또는 synthetic 경로
        #[arg(short, long)]
        file: PathBuf,
        /// 실제 소스 경로가 별도일 때 사용
        #[arg(long)]
        source: Option<PathBuf>,
        #[arg(long, default_value_t = false)]
        force: bool,
    },
    /// 감시 데몬 모드.
    Watch {
        #[arg(short, long)]
        workspace: PathBuf,
    },
}

pub(crate) fn run() -> Result<()> {
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
        Command::Index { workspace, force } => cmd_index(&workspace, force),
        Command::IndexFile { workspace, file, source, force } => {
            cmd_index_file(&workspace, &file, source.as_deref(), force)
        }
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

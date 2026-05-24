use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use clap::Parser;

use crate::config::RuntimeConfig;
use crate::idle;
use crate::router;
use crate::worker::WorkerManager;

#[derive(Parser)]
#[command(
    name = "cortex-engine",
    version,
    about = "Cortex Rust embedding runtime router"
)]
struct Args {
    #[arg(long)]
    workspace: PathBuf,

    #[arg(long)]
    worker_script: PathBuf,

    #[arg(long, default_value = "python")]
    python: String,

    #[arg(long)]
    worker_log: PathBuf,

    #[arg(long, default_value_t = 300)]
    idle_timeout_secs: u64,
}

pub fn run() -> Result<()> {
    let args = Args::parse();
    let config = RuntimeConfig {
        workspace: args.workspace,
        worker_script: args.worker_script,
        python: args.python,
        worker_log: args.worker_log,
        idle_timeout_secs: args.idle_timeout_secs,
    };
    let manager = Arc::new(WorkerManager::new(config.clone()));
    idle::spawn_idle_monitor(Arc::clone(&manager), config.idle_timeout_secs);
    router::serve(manager)
}

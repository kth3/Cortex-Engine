use anyhow::Result;
use clap::{Args, Parser, Subcommand};
use std::path::PathBuf;
use std::process::Command;
use std::thread;
use std::time::Duration;

use crate::{paths, process, relay};

#[derive(Parser)]
#[command(version, about = "Cortex runtime and indexing CLI")]
struct Cli {
    #[command(subcommand)]
    command: CommandKind,
}

#[derive(Subcommand)]
enum CommandKind {
    Start,
    Stop,
    Restart,
    Status,
    /// Index the current project or manage indexing scope.
    Index(IndexArgs),
    /// Run the file watcher in the foreground for the current project.
    Watch,
    Relay {
        #[command(subcommand)]
        command: relay::RelayCommand,
    },
}

#[derive(Args)]
struct IndexArgs {
    /// Force re-indexing even when cache says files are unchanged.
    #[arg(long, default_value_t = false)]
    force: bool,
    #[command(subcommand)]
    command: Option<IndexCommand>,
}

#[derive(Subcommand)]
enum IndexCommand {
    /// Print files selected for indexing without writing to the DB.
    Scan,
    /// Print configured indexing roots.
    Roots,
    /// Add an indexing root.
    Add {
        path: PathBuf,
        #[arg(long)]
        alias: Option<String>,
    },
    /// Remove an indexing root by path, alias, or @external/<alias>.
    Remove { target: String },
    /// Index one workspace-relative file.
    File {
        file: PathBuf,
        #[arg(long, default_value_t = false)]
        force: bool,
    },
}

pub(crate) fn run() -> Result<()> {
    match Cli::parse().command {
        CommandKind::Start => start(),
        CommandKind::Stop => stop(),
        CommandKind::Restart => {
            stop()?;
            start()
        }
        CommandKind::Status => status(),
        CommandKind::Index(args) => index(args),
        CommandKind::Watch => watch_foreground(),
        CommandKind::Relay { command } => relay::run(command),
    }
}


fn index(args: IndexArgs) -> Result<()> {
    let workspace = paths::workspace();
    let mut command = Command::new(paths::watcher_binary());
    match args.command {
        None => {
            command.arg("index").arg("--workspace").arg(&workspace);
            if args.force {
                command.arg("--force");
            }
        }
        Some(IndexCommand::Scan) => {
            command
                .arg("scan")
                .arg("--workspace")
                .arg(&workspace)
                .arg("--format")
                .arg("lines");
        }
        Some(IndexCommand::Roots) => {
            command
                .arg("index-roots")
                .arg("list")
                .arg("--workspace")
                .arg(&workspace);
        }
        Some(IndexCommand::Add { path, alias }) => {
            command
                .arg("index-roots")
                .arg("add")
                .arg(path)
                .arg("--workspace")
                .arg(&workspace);
            if let Some(alias) = alias {
                command.arg("--alias").arg(alias);
            }
        }
        Some(IndexCommand::Remove { target }) => {
            command
                .arg("index-roots")
                .arg("remove")
                .arg(target)
                .arg("--workspace")
                .arg(&workspace);
        }
        Some(IndexCommand::File { file, force }) => {
            command
                .arg("index-file")
                .arg("--workspace")
                .arg(&workspace)
                .arg("--file")
                .arg(file);
            if force || args.force {
                command.arg("--force");
            }
        }
    }
    command.env("CORTEX_WORKSPACE", &workspace);
    command.env("CORTEX_DATA_HOME", paths::data_home());
    run_status(command)
}

fn watch_foreground() -> Result<()> {
    let workspace = paths::workspace();
    let mut command = Command::new(paths::watcher_binary());
    command.arg("watch").arg("--workspace").arg(&workspace);
    command.env("CORTEX_WORKSPACE", &workspace);
    command.env("CORTEX_DATA_HOME", paths::data_home());
    run_status(command)
}

fn run_status(mut command: Command) -> Result<()> {
    let status = command.status()?;
    if status.success() {
        Ok(())
    } else {
        Err(anyhow::anyhow!("command failed with status {status}"))
    }
}

fn start() -> Result<()> {
    if service_alive(&paths::engine_pid_file()) && service_alive(&paths::watcher_pid_file()) {
        return status();
    }
    stop_stale();

    let workspace = paths::workspace();
    let mut engine = Command::new(paths::engine_binary());
    engine
        .arg("--workspace")
        .arg(&workspace)
        .arg("--worker-script")
        .arg(paths::worker_script())
        .arg("--python")
        .arg(paths::python())
        .arg("--worker-log")
        .arg(paths::worker_log())
        .env("CORTEX_WORKSPACE", &workspace)
        .env("CORTEX_DATA_HOME", paths::data_home());
    let engine_pid = process::spawn_logged(engine, &paths::engine_log())?;
    process::write_pid(&paths::engine_pid_file(), engine_pid)?;

    let mut watcher = Command::new(paths::watcher_binary());
    watcher.args(["watch", "--workspace"]).arg(&workspace);
    watcher
        .env("CORTEX_WORKSPACE", &workspace)
        .env("CORTEX_DATA_HOME", paths::data_home());
    let watcher_pid = process::spawn_logged(watcher, &paths::watcher_log())?;
    process::write_pid(&paths::watcher_pid_file(), watcher_pid)?;

    thread::sleep(Duration::from_millis(500));
    status()
}

fn stop() -> Result<()> {
    stop_one("Engine Server", &paths::engine_pid_file());
    stop_one("Watcher Daemon", &paths::watcher_pid_file());
    Ok(())
}

fn status() -> Result<()> {
    let engine_pid = process::read_pid(&paths::engine_pid_file());
    let watcher_pid = process::read_pid(&paths::watcher_pid_file());
    println!("--- Cortex Status Report (Rust Supervisor) ---");
    print_service("Engine Server", engine_pid);
    print_service("Watcher Daemon", watcher_pid);
    println!("IPC Endpoint  : {}:{}", "127.0.0.1", paths::ENGINE_PORT);
    println!("Worker Port   : {}", paths::WORKER_PORT);
    println!("----------------------------------------------");
    Ok(())
}

fn service_alive(pid_path: &std::path::Path) -> bool {
    process::read_pid(pid_path)
        .map(process::is_alive)
        .unwrap_or(false)
}

fn stop_stale() {
    if !service_alive(&paths::engine_pid_file()) {
        process::remove_pid(&paths::engine_pid_file());
    }
    if !service_alive(&paths::watcher_pid_file()) {
        process::remove_pid(&paths::watcher_pid_file());
    }
}

fn stop_one(label: &str, pid_path: &std::path::Path) {
    let Some(pid) = process::read_pid(pid_path) else {
        println!("{label}: STOPPED");
        return;
    };
    if process::is_alive(pid) {
        match process::terminate(pid) {
            Ok(()) => println!("{label}: STOPPED pid={pid}"),
            Err(err) => println!("{label}: STOP ERROR pid={pid} {err}"),
        }
    } else {
        println!("{label}: STALE pid={pid}");
    }
    process::remove_pid(pid_path);
}

fn print_service(label: &str, pid: Option<u32>) {
    match pid {
        Some(pid) if process::is_alive(pid) => println!("{label}: RUNNING pid={pid}"),
        Some(pid) => println!("{label}: STOPPED stale_pid={pid}"),
        None => println!("{label}: STOPPED"),
    }
}

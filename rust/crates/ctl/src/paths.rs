use std::env;
use std::path::{Path, PathBuf};

pub(crate) const ENGINE_PORT: u16 = 42384;
pub(crate) const WORKER_PORT: u16 = 42385;

pub(crate) fn workspace() -> PathBuf {
    env::var_os("CORTEX_WORKSPACE")
        .map(PathBuf::from)
        .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

pub(crate) fn repo_root() -> PathBuf {
    env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .and_then(|bin| {
            if bin.ends_with("release") || bin.ends_with("debug") {
                bin.parent()
                    .and_then(Path::parent)
                    .and_then(Path::parent)
                    .map(Path::to_path_buf)
            } else {
                None
            }
        })
        .unwrap_or_else(|| workspace())
}

pub(crate) fn history_dir() -> PathBuf {
    workspace().join(".cortex").join("history")
}

pub(crate) fn pid_dir() -> PathBuf {
    history_dir().join("pids")
}

pub(crate) fn engine_pid_file() -> PathBuf {
    pid_dir().join("engine.pid")
}

pub(crate) fn watcher_pid_file() -> PathBuf {
    pid_dir().join("watcher.pid")
}

pub(crate) fn engine_log() -> PathBuf {
    history_dir().join("engine_server.log")
}

pub(crate) fn watcher_log() -> PathBuf {
    history_dir().join("watcher_output.log")
}

pub(crate) fn python() -> String {
    env::var("CORTEX_PYTHON_EXECUTABLE")
        .or_else(|_| env::var("CORTEX_PYTHON_FALLBACK"))
        .unwrap_or_else(|_| "python".to_string())
}

pub(crate) fn watcher_binary() -> PathBuf {
    let name = if cfg!(windows) {
        "cortex-watcher.exe"
    } else {
        "cortex-watcher"
    };
    let root = repo_root();
    for profile in ["release", "debug"] {
        let candidate = root.join("rust").join("target").join(profile).join(name);
        if candidate.exists() {
            return candidate;
        }
    }
    env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|dir| dir.join(name)))
        .filter(|path| path.exists())
        .unwrap_or_else(|| root.join("rust").join("target").join("release").join(name))
}

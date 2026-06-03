use std::path::Path;
use std::process::Command;

use anyhow::{bail, Context, Result};
use serde::Deserialize;

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

    fn as_str(self) -> &'static str {
        match self {
            Self::Callers => "callers",
            Self::Callees => "callees",
            Self::Both => "both",
        }
    }
}

#[derive(Debug, Default, Clone, PartialEq, Eq, Deserialize)]
pub struct GraphSyncStats {
    pub nodes: usize,
    pub edges: usize,
}

#[derive(Debug, Deserialize)]
struct GraphSyncResponse {
    nodes: usize,
    edges: usize,
    #[serde(default)]
    errors: usize,
}

#[derive(Debug, Deserialize)]
struct GraphNeighborsResponse {
    neighbors: Vec<String>,
}

pub fn sync_graph_store(
    sqlite_path: impl AsRef<Path>,
    graph_path: impl AsRef<Path>,
) -> Result<GraphSyncStats> {
    let output = python_graph_command()
        .arg("-m")
        .arg("cortex.storage.graph")
        .arg("sync")
        .arg("--sqlite")
        .arg(sqlite_path.as_ref())
        .arg("--graph")
        .arg(graph_path.as_ref())
        .output()
        .context("failed to run Python Kuzu graph sync helper")?;

    if !output.status.success() {
        bail!(
            "Python Kuzu graph sync failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }

    let response: GraphSyncResponse = serde_json::from_slice(&output.stdout)
        .context("failed to parse Python Kuzu graph sync response")?;
    if response.errors > 0 {
        bail!("Python Kuzu graph sync reported {} errors", response.errors);
    }

    Ok(GraphSyncStats {
        nodes: response.nodes,
        edges: response.edges,
    })
}

pub fn sync_file_graph(
    graph_path: impl AsRef<Path>,
    _module_name: &str,
    _rel_path: &str,
    _nodes: &[cortex_parsers::NodeRecord],
    _edges: &[cortex_parsers::EdgeRecord],
) -> Result<()> {
    let graph_path = graph_path.as_ref();
    if !graph_path.exists() {
        return Ok(());
    }
    let Some(data_dir) = graph_path.parent() else {
        return Ok(());
    };
    let sqlite_path = data_dir.join("memories.db");
    if !sqlite_path.exists() {
        return Ok(());
    }
    sync_graph_store(&sqlite_path, graph_path).map(|_| ())
}

pub fn graph_neighbors(
    graph_path: impl AsRef<Path>,
    node_fqn: &str,
    direction: GraphDirection,
    limit: usize,
) -> Result<Vec<String>> {
    let output = python_graph_command()
        .arg("-m")
        .arg("cortex.storage.graph")
        .arg("neighbors")
        .arg("--graph")
        .arg(graph_path.as_ref())
        .arg("--node")
        .arg(node_fqn)
        .arg("--direction")
        .arg(direction.as_str())
        .arg("--limit")
        .arg(limit.to_string())
        .output()
        .context("failed to run Python Kuzu graph query helper")?;

    if !output.status.success() {
        return Ok(Vec::new());
    }

    let response: GraphNeighborsResponse = serde_json::from_slice(&output.stdout)
        .context("failed to parse Python Kuzu graph query response")?;
    Ok(response.neighbors)
}

fn python_graph_command() -> Command {
    let python = std::env::var("CORTEX_PYTHON").unwrap_or_else(|_| default_python());
    let mut command = Command::new(python);
    if let Ok(cwd) = std::env::current_dir() {
        let src = cwd.join("src");
        if src.exists() {
            let sep = if cfg!(windows) { ";" } else { ":" };
            let python_path = match std::env::var("PYTHONPATH") {
                Ok(existing) if !existing.is_empty() => {
                    format!("{}{}{}", src.to_string_lossy(), sep, existing)
                }
                _ => src.to_string_lossy().into_owned(),
            };
            command.env("PYTHONPATH", python_path);
        }
    }
    command
}

fn default_python() -> String {
    if let Some(venv) = std::env::var_os("VIRTUAL_ENV") {
        let exe = Path::new(&venv)
            .join(if cfg!(windows) { "Scripts" } else { "bin" })
            .join(if cfg!(windows) { "python.exe" } else { "python" });
        if exe.exists() {
            return exe.to_string_lossy().into_owned();
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        let exe = cwd
            .join(".venv")
            .join(if cfg!(windows) { "Scripts" } else { "bin" })
            .join(if cfg!(windows) { "python.exe" } else { "python" });
        if exe.exists() {
            return exe.to_string_lossy().into_owned();
        }
    }
    "python3".to_string()
}

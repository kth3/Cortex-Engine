//! Python MCP fallback transport.
//!
//! This module lets the Rust dispatcher delegate unported requests to the
//! Python MCP server over line-delimited JSON-RPC stdio.
//!
//! The child process is launched through `scripts/cortex_mcp.py` with
//! `CORTEX_MCP_FORCE_PYTHON=1`, so the wrapper skips the Rust binary and runs
//! `cortex.mcp.server:main` directly.

use anyhow::{bail, Context, Result};
use serde_json::Value;
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

const FORCE_PYTHON_ENV: &str = "CORTEX_MCP_FORCE_PYTHON";
const DISABLE_ENGINE_START_ENV: &str = "CORTEX_MCP_DISABLE_ENGINE_START";
const PYTHON_EXEC_ENV: &str = "CORTEX_PYTHON_EXECUTABLE";
const PYTHON_ENTRYPOINT: &str = "scripts/cortex_mcp.py";

/// Persistent Python fallback subprocess for delegated JSON-RPC requests.
#[derive(Debug)]
pub struct PythonFallback {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl PythonFallback {
    /// Spawn the Python MCP server wrapper in force-python mode.
    pub fn spawn() -> Result<Self> {
        let workspace_root = workspace_root();
        let entrypoint = workspace_root.join(PYTHON_ENTRYPOINT);

        if !entrypoint.exists() {
            bail!(
                "python MCP entrypoint not found at {}",
                entrypoint.display()
            );
        }

        let python = env::var(PYTHON_EXEC_ENV).unwrap_or_else(|_| "python".to_string());
        let mut command = Command::new(python);
        command
            .arg("-u")
            .arg(&entrypoint)
            .env(FORCE_PYTHON_ENV, "1")
            .env(DISABLE_ENGINE_START_ENV, "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .current_dir(&workspace_root);

        let mut child = command.spawn().with_context(|| {
            format!(
                "failed to launch python fallback at {}",
                entrypoint.display()
            )
        })?;

        let stdin = child
            .stdin
            .take()
            .context("python fallback missing stdin pipe")?;
        let stdout = child
            .stdout
            .take()
            .context("python fallback missing stdout pipe")?;

        Ok(Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
        })
    }

    /// Send one JSON-RPC request and read one JSON-RPC response line.
    pub fn request(&mut self, request: &Value) -> Result<Value> {
        let payload =
            serde_json::to_string(request).context("failed to encode fallback request")?;
        let response = self.request_line(&payload)?;
        serde_json::from_str(&response).context("failed to decode fallback response")
    }

    /// Send one raw JSON line and return the raw response line.
    pub fn request_line(&mut self, request_line: &str) -> Result<String> {
        self.stdin
            .write_all(request_line.as_bytes())
            .context("failed to write fallback request")?;
        self.stdin
            .write_all(b"\n")
            .context("failed to terminate fallback request line")?;
        self.stdin
            .flush()
            .context("failed to flush fallback request")?;

        let mut response = String::new();
        loop {
            response.clear();
            let read = self
                .stdout
                .read_line(&mut response)
                .context("failed to read fallback response")?;

            if read == 0 {
                let status = self
                    .child
                    .try_wait()
                    .context("failed to inspect fallback process status")?;
                match status {
                    Some(status) => bail!("python fallback exited with {}", status),
                    None => bail!("python fallback closed stdout unexpectedly"),
                }
            }

            let trimmed = response.trim();
            if trimmed.is_empty() {
                continue;
            }

            return Ok(trimmed.to_string());
        }
    }
}

impl Drop for PythonFallback {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn workspace_root() -> PathBuf {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .ancestors()
        .nth(3)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| manifest_dir.to_path_buf())
}

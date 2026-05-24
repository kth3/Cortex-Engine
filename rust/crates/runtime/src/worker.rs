use std::fs::{self, File};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, TryLockError};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{anyhow, Result};
use serde_json::{json, Value};

use crate::config::{RuntimeConfig, WORKER_HOST, WORKER_PORT};
use crate::protocol::{recv_msg, send_msg};

struct WorkerState {
    process: Option<Child>,
    last_activity: Instant,
}

pub struct WorkerManager {
    config: RuntimeConfig,
    state: Mutex<WorkerState>,
    request_lock: Mutex<()>,
}

impl WorkerManager {
    pub fn new(config: RuntimeConfig) -> Self {
        Self {
            config,
            state: Mutex::new(WorkerState {
                process: None,
                last_activity: Instant::now(),
            }),
            request_lock: Mutex::new(()),
        }
    }

    pub fn start_async(self: &std::sync::Arc<Self>) {
        let manager = std::sync::Arc::clone(self);
        thread::spawn(move || {
            let _ = manager.ensure_running();
        });
    }

    pub fn is_alive(&self) -> bool {
        let mut state = self.state.lock().expect("worker state poisoned");
        Self::child_alive(&mut state.process)
    }

    pub fn request_in_progress(&self) -> bool {
        match self.request_lock.try_lock() {
            Ok(_) => false,
            Err(TryLockError::WouldBlock) => true,
            Err(TryLockError::Poisoned(_)) => false,
        }
    }

    pub fn idle_for(&self) -> Duration {
        let state = self.state.lock().expect("worker state poisoned");
        state.last_activity.elapsed()
    }

    pub fn ping(&self) -> Result<Option<Value>> {
        self.send_worker_request(json!({"command": "ping"}), Duration::from_millis(1500))
    }

    pub fn forward_with_retry(&self, request: Value, attempts: usize) -> Value {
        let _guard = self.request_lock.lock().expect("request lock poisoned");
        self.touch();

        for attempt in 0..attempts {
            if let Err(err) = self.ensure_running() {
                return json!({"status": "error", "message": format!("Failed to start PyTorch worker process: {err}")});
            }

            match self.send_worker_request(request.clone(), Duration::from_secs(15)) {
                Ok(Some(response)) => return response,
                Ok(None) => self.kill(),
                Err(err) => {
                    eprintln!(
                        "[cortex-engine] worker forward failed: {err}. attempt {}/{}",
                        attempt + 1,
                        attempts
                    );
                    self.kill();
                }
            }
        }

        json!({"status": "error", "message": "Worker crashed repeatedly"})
    }

    pub fn shutdown(&self, reason: &str) {
        let mut state = self.state.lock().expect("worker state poisoned");
        if !Self::child_alive(&mut state.process) {
            state.process = None;
            return;
        }

        eprintln!("[cortex-engine] {reason}. Sending shutdown to worker...");
        let _ = self.send_worker_request(json!({"command": "shutdown"}), Duration::from_secs(3));
        if let Some(child) = state.process.as_mut() {
            for _ in 0..10 {
                if child.try_wait().ok().flatten().is_some() {
                    state.process = None;
                    return;
                }
                thread::sleep(Duration::from_millis(500));
            }
            let _ = child.kill();
            state.process = None;
        }
    }

    fn ensure_running(&self) -> Result<()> {
        let mut state = self.state.lock().expect("worker state poisoned");
        if Self::child_alive(&mut state.process) {
            return Ok(());
        }
        state.process = None;

        if let Some(parent) = self.config.worker_log.parent() {
            fs::create_dir_all(parent)?;
        }
        let stdout = File::create(&self.config.worker_log)?;
        let stderr = stdout.try_clone()?;
        let mut command = Command::new(&self.config.python);
        command
            .arg(&self.config.worker_script)
            .env("CORTEX_WORKSPACE", &self.config.workspace)
            .env("CORTEX_ENGINE_WORKER_PORT", WORKER_PORT.to_string())
            .env("PYTHONPATH", self.python_path())
            .current_dir(&self.config.workspace)
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        let child = command.spawn()?;
        state.process = Some(child);
        drop(state);

        if wait_until_listening(Duration::from_secs(30)) {
            Ok(())
        } else {
            self.kill();
            Err(anyhow!(
                "worker did not listen on {WORKER_HOST}:{WORKER_PORT}"
            ))
        }
    }

    fn send_worker_request(&self, request: Value, timeout: Duration) -> Result<Option<Value>> {
        let mut stream = TcpStream::connect((WORKER_HOST, WORKER_PORT))?;
        stream.set_read_timeout(Some(timeout))?;
        stream.set_write_timeout(Some(timeout))?;
        send_msg(&mut stream, &request)?;
        Ok(recv_msg(&mut stream)?)
    }

    fn touch(&self) {
        let mut state = self.state.lock().expect("worker state poisoned");
        state.last_activity = Instant::now();
    }

    fn kill(&self) {
        let mut state = self.state.lock().expect("worker state poisoned");
        if let Some(child) = state.process.as_mut() {
            let _ = child.kill();
        }
        state.process = None;
    }

    fn child_alive(child: &mut Option<Child>) -> bool {
        match child
            .as_mut()
            .and_then(|child| child.try_wait().ok())
            .flatten()
        {
            Some(_) => false,
            None => child.is_some(),
        }
    }

    fn python_path(&self) -> String {
        let src = self.config.workspace.join("src");
        match std::env::var("PYTHONPATH") {
            Ok(existing) if !existing.is_empty() => {
                let sep = if cfg!(windows) { ";" } else { ":" };
                format!("{}{}{}", src.to_string_lossy(), sep, existing)
            }
            _ => src.to_string_lossy().into_owned(),
        }
    }
}

fn wait_until_listening(timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect((WORKER_HOST, WORKER_PORT)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(500));
    }
    false
}

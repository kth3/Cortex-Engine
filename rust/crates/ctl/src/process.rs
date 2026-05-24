use anyhow::{anyhow, Result};
use std::fs::{self, File};
use std::path::Path;
use std::process::{Command, Stdio};

pub(crate) fn spawn_logged(mut command: Command, log_path: &Path) -> Result<u32> {
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let stdout = File::create(log_path)?;
    let stderr = stdout.try_clone()?;
    let child = command
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()?;
    Ok(child.id())
}

pub(crate) fn write_pid(path: &Path, pid: u32) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, pid.to_string())?;
    Ok(())
}

pub(crate) fn read_pid(path: &Path) -> Option<u32> {
    fs::read_to_string(path).ok()?.trim().parse().ok()
}

pub(crate) fn remove_pid(path: &Path) {
    let _ = fs::remove_file(path);
}

pub(crate) fn is_alive(pid: u32) -> bool {
    if cfg!(windows) {
        let output = Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output();
        return output
            .ok()
            .and_then(|out| String::from_utf8(out.stdout).ok())
            .map(|text| text.contains(&pid.to_string()))
            .unwrap_or(false);
    }
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

pub(crate) fn terminate(pid: u32) -> Result<()> {
    let status = if cfg!(windows) {
        Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .status()?
    } else {
        Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .status()?
    };
    if status.success() {
        Ok(())
    } else {
        Err(anyhow!("failed to terminate pid {pid}"))
    }
}

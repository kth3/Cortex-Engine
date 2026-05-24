use anyhow::{anyhow, Result};
use clap::Subcommand;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::paths;

const ZOMBIE_LOCK_THRESHOLD_SECONDS: i64 = 2 * 60 * 60;
const UNITY_RISK_SUFFIXES: &[&str] = &[".unity", ".prefab", ".asset", ".meta"];
const UNITY_RISK_EXACT_PATHS: &[&str] = &["packages/manifest.json", "packages/packages-lock.json"];
const UNITY_RISK_PREFIXES: &[&str] = &["projectsettings/"];
const UNITY_RISK_MARKER: &str = "[Unity-risk]";

#[derive(Subcommand)]
pub(crate) enum RelayCommand {
    Status {
        lane_id: Option<String>,
    },
    Acquire {
        agent_id: String,
        task_name: String,
        lane_id: Option<String>,
    },
    Release {
        agent_id: String,
        lane_id: Option<String>,
        handoff_to: Option<String>,
        message: Option<String>,
        contract_id: Option<String>,
    },
    ForceRelease {
        lane_id: Option<String>,
    },
    ClaimFiles {
        lane_id: String,
        files: Vec<String>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Board {
    updated_at: String,
    lanes: BTreeMap<String, Lane>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Lane {
    status: String,
    active_agent_id: Option<String>,
    current_task: Option<String>,
    phase: String,
    handoff_to: Option<String>,
    handoff_message: Option<String>,
    contract_id: Option<String>,
    locked_at: Option<String>,
    files_to_modify: Vec<String>,
}

pub(crate) fn run(command: RelayCommand) -> Result<()> {
    let state = RelayState::new(paths::workspace());
    match command {
        RelayCommand::Status { lane_id } => state.status(lane_id.as_deref()),
        RelayCommand::Acquire {
            agent_id,
            task_name,
            lane_id,
        } => state.acquire(
            &agent_id,
            &task_name,
            lane_id.as_deref().unwrap_or("default"),
        ),
        RelayCommand::Release {
            agent_id,
            lane_id,
            handoff_to,
            message,
            contract_id,
        } => state.release(
            &agent_id,
            lane_id.as_deref().unwrap_or("default"),
            handoff_to.as_deref(),
            message.as_deref(),
            contract_id.as_deref(),
        ),
        RelayCommand::ForceRelease { lane_id } => {
            state.force_release(lane_id.as_deref().unwrap_or("default"))
        }
        RelayCommand::ClaimFiles { lane_id, files } => state.claim_files(&lane_id, &files),
    }
}

struct RelayState {
    board_path: PathBuf,
}

impl RelayState {
    fn new(workspace: impl AsRef<Path>) -> Self {
        Self {
            board_path: board_json_path(workspace),
        }
    }

    fn status(&self, lane_id: Option<&str>) -> Result<()> {
        self.transaction(|board| {
            println!();
            println!("=== AGENT RELAY BOARD (Multi-Lane) ===");
            for (id, lane) in &board.lanes {
                if lane_id.is_some_and(|target| target != id) {
                    continue;
                }
                println!("[{} LANE]", id.to_uppercase());
                println!("  Status:   {}", lane.status);
                println!(
                    "  AgentID:  {}",
                    lane.active_agent_id.as_deref().unwrap_or("None")
                );
                println!(
                    "  Task:     {}",
                    lane.current_task.as_deref().unwrap_or("None")
                );
                println!("  Phase:    {}", lane.phase);
                if let Some(next) = &lane.handoff_to {
                    println!("  Next:     {next}");
                }
                if let Some(contract) = &lane.contract_id {
                    println!("  Contract: {contract}");
                }
                if let Some(message) = &lane.handoff_message {
                    println!("  Message:  \"{message}\"");
                }
                if let Some(locked_at) = &lane.locked_at {
                    println!("  Locked:   {locked_at}");
                }
                if !lane.files_to_modify.is_empty() {
                    println!(
                        "  Files:    {}",
                        format_file_claims(&lane.files_to_modify).join(", ")
                    );
                }
                if is_zombie(lane, &board.updated_at) {
                    println!(
                        "  [WARNING] Potential Zombie Lock detected! (>{}h)",
                        ZOMBIE_LOCK_THRESHOLD_SECONDS / 3600
                    );
                }
                println!("{}", "-".repeat(30));
            }
            println!("Updated:  {}", board.updated_at);
            println!();
            Ok(false)
        })?;
        Ok(())
    }

    fn acquire(&self, agent_id: &str, task_name: &str, lane_id: &str) -> Result<()> {
        self.transaction(|board| {
            let updated_at = board.updated_at.clone();
            let lane = board
                .lanes
                .entry(lane_id.to_string())
                .or_insert_with(default_lane);

            if lane.status == "HANDOFF" {
                if lane.handoff_to.as_deref().is_some_and(|expected| expected != agent_id) {
                    return Err(anyhow!(
                        "Lane '{lane_id}' is in HANDOFF state waiting for '{}', but '{agent_id}' tried to acquire.",
                        lane.handoff_to.as_deref().unwrap_or("")
                    ));
                }
                lane.status = "IDLE".to_string();
                lane.handoff_to = None;
                println!("[HANDOFF-ACCEPT] Lane '{lane_id}' handoff accepted by '{agent_id}'.");
            } else if lane.status != "IDLE" && lane.active_agent_id.as_deref() != Some(agent_id) {
                if is_zombie(lane, &updated_at) {
                    evict_zombie(lane_id, lane);
                } else {
                    return Err(anyhow!(
                        "Lane '{lane_id}' is occupied by {} working on '{}'.",
                        lane.active_agent_id.as_deref().unwrap_or("unknown"),
                        lane.current_task.as_deref().unwrap_or("unknown")
                    ));
                }
            }

            lane.active_agent_id = Some(agent_id.to_string());
            lane.current_task = Some(task_name.to_string());
            lane.status = "BUSY".to_string();
            lane.handoff_message = None;
            lane.locked_at = Some(now_text());
            lane.files_to_modify = normalize_files(&lane.files_to_modify);
            println!("[LOCKED] Agent '{agent_id}' acquired lane '{lane_id}' for task '{task_name}'.");
            if let Some(contract_id) = &lane.contract_id {
                println!("[CONTRACT] Previous contract on file: {contract_id}");
                println!("           Read it before starting: .cortex/artifacts/{contract_id}");
            }
            Ok(true)
        })?;
        Ok(())
    }

    fn release(
        &self,
        agent_id: &str,
        lane_id: &str,
        handoff_to: Option<&str>,
        message: Option<&str>,
        contract_id: Option<&str>,
    ) -> Result<()> {
        self.transaction(|board| {
            let Some(lane) = board.lanes.get_mut(lane_id) else {
                return Err(anyhow!("Lane '{lane_id}' does not exist."));
            };
            if lane.active_agent_id.as_deref() != Some(agent_id) {
                return Err(anyhow!(
                    "Agent '{agent_id}' does not hold the lock for lane '{lane_id}'."
                ));
            }

            lane.status = if handoff_to.is_some() {
                "HANDOFF"
            } else {
                "IDLE"
            }
            .to_string();
            lane.phase = "DONE".to_string();
            lane.handoff_to = handoff_to.map(str::to_string);
            lane.handoff_message = message.map(limit_message);
            lane.contract_id = contract_id.map(str::to_string);
            lane.locked_at = None;
            lane.files_to_modify.clear();
            lane.active_agent_id = None;
            if handoff_to.is_none() {
                lane.current_task = None;
            }
            println!(
                "[RELEASED] Agent '{agent_id}' finished task on lane '{lane_id}'. Next: {}",
                handoff_to.unwrap_or("NONE")
            );
            Ok(true)
        })?;
        Ok(())
    }

    fn force_release(&self, lane_id: &str) -> Result<()> {
        self.transaction(|board| {
            let Some(lane) = board.lanes.get_mut(lane_id) else {
                return Err(anyhow!("Lane '{lane_id}' does not exist."));
            };
            let old_agent = lane.active_agent_id.as_deref().unwrap_or("unknown").to_string();
            lane.status = "IDLE".to_string();
            lane.active_agent_id = None;
            lane.current_task = None;
            lane.phase = "FORCE_RELEASED".to_string();
            lane.handoff_to = None;
            lane.handoff_message = Some(format!("Force-released by operator (was: {old_agent})"));
            lane.locked_at = None;
            lane.files_to_modify.clear();
            println!(
                "[FORCE-RELEASED] Lane '{lane_id}' has been forcefully released. (was held by: {old_agent})"
            );
            Ok(true)
        })?;
        Ok(())
    }

    fn claim_files(&self, lane_id: &str, files: &[String]) -> Result<()> {
        let normalized = normalize_files(files);
        self.transaction(|board| {
            let conflicts = find_file_claim_conflicts(board, lane_id, &normalized);
            if !conflicts.is_empty() {
                let detail = conflicts
                    .into_iter()
                    .map(|(path, owner)| {
                        format!("{} held by lane '{owner}'", format_file_claim(&path))
                    })
                    .collect::<Vec<_>>()
                    .join("; ");
                return Err(anyhow!(
                    "File claim conflict for lane '{lane_id}': {detail}"
                ));
            }

            let lane = board
                .lanes
                .entry(lane_id.to_string())
                .or_insert_with(default_lane);
            lane.files_to_modify = normalized.clone();
            println!(
                "[CLAIMED] Lane '{lane_id}' reserved files: {}",
                format_file_claims(&normalized).join(", ")
            );
            Ok(true)
        })?;
        Ok(())
    }

    fn transaction(&self, mut f: impl FnMut(&mut Board) -> Result<bool>) -> Result<Board> {
        let _lock = FileLock::acquire(self.lock_path())?;
        if let Some(parent) = self.board_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut board = fs::read_to_string(&self.board_path)
            .ok()
            .and_then(|text| serde_json::from_str::<Board>(&text).ok())
            .unwrap_or_else(default_board);
        ensure_board_schema(&mut board);
        let should_write = f(&mut board)?;
        if should_write {
            board.updated_at = now_text();
            fs::write(&self.board_path, serde_json::to_string_pretty(&board)?)?;
        }
        Ok(board)
    }

    fn lock_path(&self) -> PathBuf {
        self.board_path.with_extension("lock")
    }
}

struct FileLock {
    path: PathBuf,
}

impl FileLock {
    fn acquire(path: PathBuf) -> Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        for _ in 0..200 {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(_) => return Ok(Self { path }),
                Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {
                    thread::sleep(Duration::from_millis(50));
                }
                Err(err) => return Err(err.into()),
            }
        }
        Err(anyhow!("Timed out waiting for relay board lock"))
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

fn default_board() -> Board {
    Board {
        updated_at: now_text(),
        lanes: BTreeMap::from([("default".to_string(), default_lane())]),
    }
}

fn default_lane() -> Lane {
    Lane {
        status: "IDLE".to_string(),
        active_agent_id: None,
        current_task: None,
        phase: "READY".to_string(),
        handoff_to: None,
        handoff_message: None,
        contract_id: None,
        locked_at: None,
        files_to_modify: Vec::new(),
    }
}

fn ensure_board_schema(board: &mut Board) {
    if board.lanes.is_empty() {
        board.lanes.insert("default".to_string(), default_lane());
    }
    for lane in board.lanes.values_mut() {
        lane.files_to_modify = normalize_files(&lane.files_to_modify);
    }
}

fn normalize_file_path(path: &str) -> Option<String> {
    let trimmed = path.trim();
    if trimmed.is_empty() || trimmed == "." {
        return None;
    }
    let normalized = Path::new(trimmed)
        .components()
        .collect::<PathBuf>()
        .to_string_lossy()
        .replace('\\', "/");
    let normalized = if cfg!(windows) {
        normalized.to_lowercase()
    } else {
        normalized
    };
    if normalized.is_empty() || normalized == "." {
        None
    } else {
        Some(normalized)
    }
}

fn normalize_files(files: &[String]) -> Vec<String> {
    let mut seen = BTreeSet::new();
    files
        .iter()
        .filter_map(|path| normalize_file_path(path))
        .filter(|path| seen.insert(path.clone()))
        .collect()
}

fn is_unity_risk_file(path: &str) -> bool {
    normalize_file_path(path).is_some_and(|normalized| {
        UNITY_RISK_SUFFIXES
            .iter()
            .any(|suffix| normalized.ends_with(suffix))
            || UNITY_RISK_EXACT_PATHS.contains(&normalized.as_str())
            || UNITY_RISK_PREFIXES
                .iter()
                .any(|prefix| normalized.starts_with(prefix))
    })
}

fn format_file_claim(path: &str) -> String {
    if is_unity_risk_file(path) {
        format!("{path} {UNITY_RISK_MARKER}")
    } else {
        path.to_string()
    }
}

fn format_file_claims(files: &[String]) -> Vec<String> {
    normalize_files(files)
        .iter()
        .map(|path| format_file_claim(path))
        .collect()
}

fn find_file_claim_conflicts(
    board: &Board,
    lane_id: &str,
    requested_files: &[String],
) -> Vec<(String, String)> {
    let requested = requested_files.iter().collect::<BTreeSet<_>>();
    if requested.is_empty() {
        return Vec::new();
    }
    let mut conflicts = Vec::new();
    for (other_lane_id, lane) in &board.lanes {
        if other_lane_id == lane_id || lane.status != "BUSY" {
            continue;
        }
        for path in normalize_files(&lane.files_to_modify) {
            if requested.contains(&path) {
                conflicts.push((path, other_lane_id.clone()));
            }
        }
    }
    conflicts
}

fn is_zombie(lane: &Lane, updated_at: &str) -> bool {
    if lane.status != "BUSY" {
        return false;
    }
    let Some(ts) = lane.locked_at.as_deref().or(Some(updated_at)) else {
        return false;
    };
    now_unix() - parse_time(ts).unwrap_or_else(now_unix) > ZOMBIE_LOCK_THRESHOLD_SECONDS
}

fn evict_zombie(lane_id: &str, lane: &mut Lane) {
    let old_agent = lane
        .active_agent_id
        .as_deref()
        .unwrap_or("unknown")
        .to_string();
    let old_task = lane
        .current_task
        .as_deref()
        .unwrap_or("unknown")
        .to_string();
    lane.status = "IDLE".to_string();
    lane.active_agent_id = None;
    lane.current_task = None;
    lane.phase = "ZOMBIE_EVICTED".to_string();
    lane.handoff_to = None;
    lane.handoff_message = Some(format!(
        "Auto-evicted zombie lock (was: {old_agent} on '{old_task}')"
    ));
    lane.locked_at = None;
    lane.files_to_modify.clear();
    println!(
        "[ZOMBIE-EVICT] Lane '{lane_id}' auto-released: agent '{old_agent}' exceeded {}h timeout.",
        ZOMBIE_LOCK_THRESHOLD_SECONDS / 3600
    );
}

fn board_json_path(workspace: impl AsRef<Path>) -> PathBuf {
    let workspace = absolute_path(workspace);
    paths::data_home()
        .join("workspaces")
        .join(workspace_key(&workspace))
        .join("state")
        .join("board.json")
}

fn workspace_key(workspace: &Path) -> String {
    if let Some(value) = std::env::var_os("CORTEX_WORKSPACE_KEY") {
        let value = value.to_string_lossy().trim().to_string();
        if !value.is_empty() {
            return value;
        }
    }
    sha1_hex(workspace.to_string_lossy().as_bytes())[..12].to_string()
}

fn absolute_path(path: impl AsRef<Path>) -> PathBuf {
    let path = path.as_ref();
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    }
}

fn now_text() -> String {
    now_unix().to_string()
}

fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

fn parse_time(value: &str) -> Option<i64> {
    if let Ok(unix) = value.parse::<i64>() {
        return Some(unix);
    }
    None
}

fn limit_message(message: &str) -> String {
    let mut chars = message.chars().collect::<Vec<_>>();
    if chars.len() <= 250 {
        return message.to_string();
    }
    chars.truncate(247);
    chars.into_iter().collect::<String>() + "..."
}

fn sha1_hex(input: &[u8]) -> String {
    fn left_rotate(value: u32, bits: u32) -> u32 {
        (value << bits) | (value >> (32 - bits))
    }

    let mut data = input.to_vec();
    let bit_len = (data.len() as u64) * 8;
    data.push(0x80);
    while (data.len() % 64) != 56 {
        data.push(0);
    }
    data.extend_from_slice(&bit_len.to_be_bytes());

    let mut h0: u32 = 0x67452301;
    let mut h1: u32 = 0xEFCDAB89;
    let mut h2: u32 = 0x98BADCFE;
    let mut h3: u32 = 0x10325476;
    let mut h4: u32 = 0xC3D2E1F0;

    for chunk in data.chunks(64) {
        let mut words = [0_u32; 80];
        for (i, word) in words.iter_mut().take(16).enumerate() {
            let offset = i * 4;
            *word = u32::from_be_bytes([
                chunk[offset],
                chunk[offset + 1],
                chunk[offset + 2],
                chunk[offset + 3],
            ]);
        }
        for i in 16..80 {
            words[i] = left_rotate(
                words[i - 3] ^ words[i - 8] ^ words[i - 14] ^ words[i - 16],
                1,
            );
        }

        let mut a = h0;
        let mut b = h1;
        let mut c = h2;
        let mut d = h3;
        let mut e = h4;

        for (i, word) in words.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A827999),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDC),
                _ => (b ^ c ^ d, 0xCA62C1D6),
            };
            let temp = left_rotate(a, 5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*word);
            e = d;
            d = c;
            c = left_rotate(b, 30);
            b = a;
            a = temp;
        }

        h0 = h0.wrapping_add(a);
        h1 = h1.wrapping_add(b);
        h2 = h2.wrapping_add(c);
        h3 = h3.wrapping_add(d);
        h4 = h4.wrapping_add(e);
    }

    format!("{h0:08x}{h1:08x}{h2:08x}{h3:08x}{h4:08x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_workspace(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("cortex-relay-test-{name}-{}", now_unix()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn claim_files_blocks_busy_lane_overlap() {
        let workspace = temp_workspace("conflict");
        let state = RelayState {
            board_path: workspace.join("board.json"),
        };
        state.acquire("agent-a", "task-a", "lane-a").unwrap();
        state
            .claim_files("lane-a", &["Assets/Foo.prefab".to_string()])
            .unwrap();
        let err = state
            .claim_files("lane-b", &["assets/foo.prefab".to_string()])
            .unwrap_err()
            .to_string();
        assert!(err.contains("File claim conflict"));
        assert!(err.contains("[Unity-risk]"));
    }

    #[test]
    fn release_requires_lane_owner() {
        let workspace = temp_workspace("owner");
        let state = RelayState {
            board_path: workspace.join("board.json"),
        };
        state.acquire("agent-a", "task-a", "default").unwrap();
        let err = state
            .release("agent-b", "default", None, None, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("does not hold the lock"));
    }

    #[test]
    fn handoff_accepts_expected_agent() {
        let workspace = temp_workspace("handoff");
        let state = RelayState {
            board_path: workspace.join("board.json"),
        };
        state.acquire("agent-a", "task-a", "default").unwrap();
        state
            .release(
                "agent-a",
                "default",
                Some("agent-b"),
                Some("next"),
                Some("c1"),
            )
            .unwrap();
        state.acquire("agent-b", "task-b", "default").unwrap();
    }
}

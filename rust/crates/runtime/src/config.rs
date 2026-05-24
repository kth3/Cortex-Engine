use std::path::PathBuf;

pub const ENGINE_HOST: &str = "127.0.0.1";
pub const WORKER_HOST: &str = "127.0.0.1";
pub const ENGINE_PORT: u16 = 42384;
pub const WORKER_PORT: u16 = 42385;

#[derive(Clone, Debug)]
pub struct RuntimeConfig {
    pub workspace: PathBuf,
    pub worker_script: PathBuf,
    pub python: String,
    pub worker_log: PathBuf,
    pub idle_timeout_secs: u64,
}

use std::sync::Arc;
use std::thread;
use std::time::Duration;

use crate::worker::WorkerManager;

pub fn spawn_idle_monitor(manager: Arc<WorkerManager>, timeout_secs: u64) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(10));
        if !manager.is_alive() {
            continue;
        }
        if manager.request_in_progress() {
            continue;
        }
        if manager.idle_for() > Duration::from_secs(timeout_secs) {
            manager.shutdown(&format!("IDLE timeout ({timeout_secs}s) reached"));
        }
    });
}

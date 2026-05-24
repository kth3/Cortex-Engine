use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{anyhow, Result};
use serde_json::json;

use crate::config::{ENGINE_HOST, ENGINE_PORT};
use crate::protocol::{recv_msg, send_msg};
use crate::worker::WorkerManager;

pub fn serve(manager: Arc<WorkerManager>) -> Result<()> {
    let listener = bind_with_retry(Duration::from_secs(20))?;
    eprintln!("[cortex-engine] listening on {ENGINE_HOST}:{ENGINE_PORT}");
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let manager = Arc::clone(&manager);
                thread::spawn(move || handle_client(stream, manager));
            }
            Err(err) => eprintln!("[cortex-engine] accept failed: {err}"),
        }
    }
    Ok(())
}

fn bind_with_retry(timeout: Duration) -> Result<TcpListener> {
    let start = Instant::now();
    loop {
        match TcpListener::bind((ENGINE_HOST, ENGINE_PORT)) {
            Ok(listener) => return Ok(listener),
            Err(err) if start.elapsed() < timeout => {
                eprintln!("[cortex-engine] port {ENGINE_PORT} not ready: {err}");
                thread::sleep(Duration::from_millis(500));
            }
            Err(err) => return Err(anyhow!("failed to bind {ENGINE_HOST}:{ENGINE_PORT}: {err}")),
        }
    }
}

fn handle_client(mut stream: TcpStream, manager: Arc<WorkerManager>) {
    let request = match recv_msg(&mut stream) {
        Ok(Some(value)) => value,
        Ok(None) => return,
        Err(err) => {
            let _ = send_msg(
                &mut stream,
                &json!({"status": "error", "message": err.to_string()}),
            );
            return;
        }
    };

    let command = request
        .get("command")
        .and_then(|value| value.as_str())
        .unwrap_or("embed");

    if command == "ping" {
        if !manager.is_alive() {
            manager.start_async();
            let _ = send_msg(
                &mut stream,
                &json!({"status": "loading", "message": "Worker is being started"}),
            );
            return;
        }
        let response =
            manager.ping().ok().flatten().unwrap_or_else(
                || json!({"status": "error", "message": "Empty response from worker"}),
            );
        let _ = send_msg(&mut stream, &response);
        return;
    }

    let response = manager.forward_with_retry(request, 2);
    let _ = send_msg(&mut stream, &response);
}

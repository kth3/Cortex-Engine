//! Cortex parser bridge.
//!
//! Parser implementations live in Python to avoid compiling tree-sitter grammars,
//! PDF extraction, and other native parser dependencies in the Rust build graph.

use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

pub mod common;

pub use common::{
    truncate, unresolved_fqn, unresolved_name, uuid5_for, EdgeRecord, NodeRecord, ParseResult,
    NAMESPACE_URL, UNRESOLVED_FQN_PREFIX, UNRESOLVED_NAME_PREFIX,
};

pub mod chunking {
    #[derive(Debug, Clone, Copy)]
    pub struct ChunkConfig {
        pub max_len: usize,
        pub overlap: usize,
    }

    pub fn semantic_chunk(text: &str) -> Vec<String> {
        semantic_chunk_with(text, ChunkConfig { max_len: 2500, overlap: 400 })
    }

    pub fn semantic_chunk_config(max_len: usize, overlap: usize) -> ChunkConfig {
        ChunkConfig { max_len, overlap }
    }

    pub fn semantic_chunk_with(text: &str, config: ChunkConfig) -> Vec<String> {
        if text.is_empty() {
            return Vec::new();
        }
        if text.chars().count() <= config.max_len {
            return vec![text.to_string()];
        }
        let chars: Vec<char> = text.chars().collect();
        let mut chunks = Vec::new();
        let mut start = 0;
        while start < chars.len() {
            let end = (start + config.max_len).min(chars.len());
            chunks.push(chars[start..end].iter().collect());
            if end == chars.len() {
                break;
            }
            start = end.saturating_sub(config.overlap.min(end));
        }
        chunks
    }
}

pub use chunking::{semantic_chunk, semantic_chunk_config, semantic_chunk_with, ChunkConfig};

pub fn parse_python_file(file_path: &str, source: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_csharp_file(file_path: &str, source: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_java_file(file_path: &str, source: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_c_file(file_path: &str, source: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_ts_file(file_path: &str, source: &str, _language: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_markdown_file(file_path: &str, source: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_html_file(file_path: &str, source: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_css_file(file_path: &str, source: &str) -> ParseResult {
    parse_source(file_path, source)
}

pub fn parse_pdf_file(file_path: &str, file: &Path) -> ParseResult {
    let output = python_parser_command()
        .arg("-m")
        .arg("cortex.parsers.bridge")
        .arg("--path")
        .arg(file_path)
        .arg("--file")
        .arg(file)
        .output();
    parse_output(output)
}

fn parse_source(file_path: &str, source: &str) -> ParseResult {
    let mut child = match python_parser_command()
        .arg("-m")
        .arg("cortex.parsers.bridge")
        .arg("--path")
        .arg(file_path)
        .arg("--stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return ParseResult::default(),
    };

    if let Some(mut stdin) = child.stdin.take() {
        if stdin.write_all(source.as_bytes()).is_err() {
            return ParseResult::default();
        }
    }

    parse_output(child.wait_with_output())
}

fn parse_output(output: std::io::Result<std::process::Output>) -> ParseResult {
    let Ok(output) = output else {
        return ParseResult::default();
    };
    if !output.status.success() {
        return ParseResult::default();
    }
    serde_json::from_slice(&output.stdout).unwrap_or_default()
}

fn python_parser_command() -> Command {
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

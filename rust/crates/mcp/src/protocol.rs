use std::env;
use std::io::{self, BufRead, Write};

use serde_json::{json, Value};

use crate::{catalog, fallback::PythonFallback, storage_tools};

const PROTOCOL_VERSION: &str = "2025-11-25";
const SERVER_NAME: &str = "Cortex-Hooks";
const SERVER_VERSION: &str = "3.8.0";
const JSONRPC_VERSION: &str = "2.0";
const METHOD_NOT_FOUND: i64 = -32601;

pub fn run_stdio<R, W>(reader: R, mut writer: W) -> io::Result<()>
where
    R: BufRead,
    W: Write,
{
    for line in reader.lines() {
        let line = line?;
        if let Some(response) = handle_line(&line) {
            serde_json::to_writer(&mut writer, &response).map_err(io::Error::other)?;
            writer.write_all(b"\n")?;
            writer.flush()?;
        }
    }

    Ok(())
}

fn handle_line(line: &str) -> Option<Value> {
    let message: Value = serde_json::from_str(line).ok()?;
    handle_message(message)
}

fn handle_message(message: Value) -> Option<Value> {
    let obj = message.as_object()?;
    let method = obj.get("method")?.as_str()?;
    let id = obj.get("id").cloned();
    let params = obj.get("params").cloned().unwrap_or_else(|| json!({}));

    match method {
        "initialize" => id.map(|id| response_ok(id, initialize_result())),
        "tools/list" => id.map(|id| response_ok(id, json!({ "tools": catalog::list_tools() }))),
        "tools/call" => dispatch_tools_call(id, params),
        _ => id.map(|id| response_ok(id, json!({}))),
    }
}

fn initialize_result() -> Value {
    json!({
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    })
}

fn dispatch_tools_call(id: Option<Value>, params: Value) -> Option<Value> {
    let id = id?;
    let Some(name) = params.get("name").and_then(Value::as_str) else {
        return Some(error_text_response(id, "Error: missing tool name"));
    };
    let arguments = params
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));

    match dispatch_native_tool(name, &arguments) {
        Some(Ok(value)) => Some(text_response(id, value)),
        Some(Err(message)) => Some(error_text_response(id, &format!("Error: {message}"))),
        None if name.starts_with("pc_") => Some(response_error(
            id,
            METHOD_NOT_FOUND,
            &format!("Unknown tool: {name}"),
        )),
        None => fallback_tools_call(id, name, arguments),
    }
}

fn dispatch_native_tool(name: &str, args: &Value) -> Option<Result<Value, String>> {
    let workspace = workspace();
    match name {
        "get_index_status" => Some(storage_tools::call_get_index_status(&workspace)),
        "read_file_with_hash" => Some(storage_tools::call_read_file_with_hash(
            &workspace,
            required_str(args, "file_path"),
        )),
        "get_file_outline" => Some(storage_tools::call_get_file_outline(
            &workspace,
            required_str(args, "file_path"),
            args.get("detail").and_then(Value::as_str),
        )),
        "resolve_symbol" => Some(storage_tools::call_resolve_symbol(
            &workspace,
            required_str(args, "name"),
            args.get("file_path").and_then(Value::as_str),
            args.get("language").and_then(Value::as_str),
            args.get("limit")
                .and_then(Value::as_u64)
                .map(|value| value as usize),
        )),
        "get_impact_graph" => Some(storage_tools::call_get_impact_graph(
            &workspace,
            required_str(args, "fqn"),
            args.get("direction").and_then(Value::as_str),
            args.get("max_depth")
                .and_then(Value::as_u64)
                .map(|value| value as u32),
            args.get("max_nodes")
                .and_then(Value::as_u64)
                .map(|value| value as u32),
        )),
        "find_execution_path" => Some(storage_tools::call_find_execution_path(
            &workspace,
            required_str(args, "from_fqn"),
            required_str(args, "to_fqn"),
            args.get("max_depth")
                .and_then(Value::as_u64)
                .map(|value| value as u32),
            args.get("max_nodes")
                .and_then(Value::as_u64)
                .map(|value| value as u32),
        )),
        "get_session_context" => Some(storage_tools::call_get_session_context(
            &workspace,
            args.get("token_budget")
                .and_then(Value::as_u64)
                .map(|value| value as u32),
        )),
        _ => None,
    }
}

fn fallback_tools_call(id: Value, name: &str, arguments: Value) -> Option<Value> {
    let request = json!({
        "jsonrpc": JSONRPC_VERSION,
        "id": id,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
        },
    });
    match PythonFallback::spawn().and_then(|mut fallback| fallback.request(&request)) {
        Ok(response) => Some(response),
        Err(err) => Some(error_text_response(
            request["id"].clone(),
            &format!("Error: {err}"),
        )),
    }
}

fn workspace() -> String {
    env::var("CORTEX_WORKSPACE")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            env::current_dir()
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_else(|_| ".".to_string())
        })
}

fn required_str<'a>(args: &'a Value, key: &str) -> &'a str {
    args.get(key).and_then(Value::as_str).unwrap_or("")
}

fn response_ok(id: Value, result: Value) -> Value {
    json!({
        "jsonrpc": JSONRPC_VERSION,
        "id": id,
        "result": result,
    })
}

fn response_error(id: Value, code: i64, message: &str) -> Value {
    json!({
        "jsonrpc": JSONRPC_VERSION,
        "id": id,
        "error": {
            "code": code,
            "message": message,
        },
    })
}

fn text_response(id: Value, value: Value) -> Value {
    let text = match value {
        Value::String(text) => text,
        other => serde_json::to_string_pretty(&other).unwrap_or_else(|_| other.to_string()),
    };
    response_ok(
        id,
        json!({
            "content": [{
                "type": "text",
                "text": text,
            }],
        }),
    )
}

fn error_text_response(id: Value, text: &str) -> Value {
    response_ok(
        id,
        json!({
            "isError": true,
            "content": [{
                "type": "text",
                "text": text,
            }],
        }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn initialize_returns_protocol_and_server_info() {
        let response = handle_line(r#"{"jsonrpc":"2.0","id":1,"method":"initialize"}"#)
            .expect("initialize should respond");

        assert_eq!(response["jsonrpc"], JSONRPC_VERSION);
        assert_eq!(response["id"], 1);
        assert_eq!(response["result"]["protocolVersion"], PROTOCOL_VERSION);
        assert_eq!(response["result"]["capabilities"], json!({"tools": {}}));
        assert_eq!(response["result"]["serverInfo"]["name"], SERVER_NAME);
        assert_eq!(response["result"]["serverInfo"]["version"], SERVER_VERSION);
    }

    #[test]
    fn tools_list_returns_catalog() {
        let response = handle_line(r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#)
            .expect("tools/list should respond");
        assert_eq!(response["result"]["tools"].as_array().unwrap().len(), 19);
    }

    #[test]
    fn unknown_method_with_id_returns_empty_result() {
        let response = handle_line(r#"{"jsonrpc":"2.0","id":"x","method":"nope"}"#)
            .expect("unknown method with id should respond");

        assert_eq!(response["jsonrpc"], JSONRPC_VERSION);
        assert_eq!(response["id"], "x");
        assert_eq!(response["result"], json!({}));
        assert!(response.get("error").is_none());
    }

    #[test]
    fn malformed_json_is_ignored_in_stdio_loop() {
        let input = Cursor::new(
            b"{not-json}\n{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"initialize\"}\n".as_slice(),
        );
        let mut output = Vec::new();

        run_stdio(input, &mut output).expect("stdio loop should ignore malformed input");

        let text = String::from_utf8(output).expect("response should be utf-8");
        let mut lines = text.lines();
        let first = lines.next().expect("initialize response expected");
        assert!(lines.next().is_none());

        let response: Value = serde_json::from_str(first).expect("response should parse");
        assert_eq!(response["id"], 7);
        assert_eq!(response["result"]["protocolVersion"], PROTOCOL_VERSION);
    }

    #[test]
    fn unknown_notification_is_ignored() {
        assert!(handle_line(r#"{"jsonrpc":"2.0","method":"nope"}"#).is_none());
    }

    #[test]
    fn old_pc_tool_returns_method_not_found_without_fallback() {
        let response = handle_line(
            r#"{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"pc_capsule","arguments":{}}}"#,
        )
        .expect("old tool should respond");
        assert_eq!(response["error"]["code"], METHOD_NOT_FOUND);
    }
}

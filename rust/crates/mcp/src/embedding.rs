use super::*;

const ENGINE_HOST: &str = "127.0.0.1";
const ENGINE_PORT: u16 = 42384;
const ENGINE_TIMEOUT_MS: u64 = 1500;

pub(crate) fn search_nodes_vec(
    conn: &Connection,
    query: &str,
    limit: usize,
) -> Result<Vec<Node>, String> {
    let Some(embedding) = query_embedding_bytes(query)? else {
        return Ok(Vec::new());
    };
    let rowids = vector_rowids(conn, "vec_nodes", &embedding, limit)?;
    let mut nodes = Vec::new();
    for rowid in rowids {
        if let Some(node) = conn
            .query_row(
                "SELECT * FROM nodes WHERE rowid = ?1",
                params![rowid],
                node_from_row,
            )
            .optional()
            .map_err(|err| err.to_string())?
        {
            nodes.push(node);
        }
    }
    Ok(nodes)
}

pub(crate) fn search_memories_vec(
    conn: &Connection,
    query: &str,
    category: Option<&str>,
    limit: usize,
) -> Result<Vec<MemoryHit>, String> {
    let Some(embedding) = query_embedding_bytes(query)? else {
        return Ok(Vec::new());
    };
    let rowids = vector_rowids(conn, "vec_memories", &embedding, limit)?;
    let mut memories = Vec::new();
    for rowid in rowids {
        let memory = conn
            .query_row(
                "SELECT key, category, content FROM memories WHERE rowid = ?1",
                params![rowid],
                |row| {
                    Ok(MemoryHit {
                        key: row.get(0)?,
                        category: row.get(1)?,
                        content: row.get(2)?,
                    })
                },
            )
            .optional()
            .map_err(|err| err.to_string())?;
        if let Some(memory) = memory {
            if category.is_none() || category == Some(memory.category.as_str()) {
                memories.push(memory);
            }
        }
    }
    Ok(memories)
}

#[derive(Debug, Clone)]
pub(crate) struct MemoryHit {
    pub(crate) key: String,
    pub(crate) category: String,
    pub(crate) content: String,
}

fn vector_rowids(
    conn: &Connection,
    table: &str,
    embedding: &[u8],
    limit: usize,
) -> Result<Vec<i64>, String> {
    let sql = format!("SELECT rowid FROM {table} WHERE embedding MATCH ?1 AND k = ?2");
    let mut stmt = match conn.prepare(&sql) {
        Ok(stmt) => stmt,
        Err(err) if is_vec_unavailable(&err) => return Ok(Vec::new()),
        Err(err) => return Err(err.to_string()),
    };
    let rows = match stmt.query_map(params![embedding, limit as i64], |row| row.get::<_, i64>(0)) {
        Ok(rows) => rows,
        Err(err) if is_vec_unavailable(&err) => return Ok(Vec::new()),
        Err(err) => return Err(err.to_string()),
    };
    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|err| err.to_string())?);
    }
    Ok(out)
}

fn query_embedding_bytes(query: &str) -> Result<Option<Vec<u8>>, String> {
    if query.trim().is_empty() {
        return Ok(None);
    }
    let mut stream = match TcpStream::connect((ENGINE_HOST, ENGINE_PORT)) {
        Ok(stream) => stream,
        Err(_) => return Ok(None),
    };
    let timeout = Duration::from_millis(ENGINE_TIMEOUT_MS);
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|err| err.to_string())?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|err| err.to_string())?;
    let request = json!({"command": "embed", "texts": [query]});
    send_json(&mut stream, &request)?;
    let response = recv_json(&mut stream)?;
    if response.get("status").and_then(Value::as_str) != Some("ok") {
        return Ok(None);
    }
    let Some(values) = response
        .get("embeddings")
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .and_then(Value::as_array)
    else {
        return Ok(None);
    };
    Ok(Some(floats_to_le_bytes(values)))
}

fn send_json(stream: &mut TcpStream, value: &Value) -> Result<(), String> {
    let bytes = serde_json::to_vec(value).map_err(|err| err.to_string())?;
    stream
        .write_all(&(bytes.len() as u32).to_be_bytes())
        .map_err(|err| err.to_string())?;
    stream.write_all(&bytes).map_err(|err| err.to_string())
}

fn recv_json(stream: &mut TcpStream) -> Result<Value, String> {
    let mut header = [0_u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|err| err.to_string())?;
    let len = u32::from_be_bytes(header) as usize;
    let mut data = vec![0_u8; len];
    stream
        .read_exact(&mut data)
        .map_err(|err| err.to_string())?;
    serde_json::from_slice(&data).map_err(|err| err.to_string())
}

fn floats_to_le_bytes(values: &[Value]) -> Vec<u8> {
    let mut out = Vec::with_capacity(values.len() * std::mem::size_of::<f32>());
    for value in values {
        let f = value.as_f64().unwrap_or(0.0) as f32;
        out.extend_from_slice(&f.to_le_bytes());
    }
    out
}

fn is_vec_unavailable(err: &rusqlite::Error) -> bool {
    let text = err.to_string().to_lowercase();
    text.contains("vec") || text.contains("no such table")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_are_serialized_like_numpy_float32_tobytes() {
        let bytes = floats_to_le_bytes(&[json!(1.0), json!(-2.5)]);
        assert_eq!(bytes, [0, 0, 128, 63, 0, 0, 32, 192]);
    }
}

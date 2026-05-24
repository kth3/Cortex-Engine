use std::io::{Read, Write};
use std::net::TcpStream;

use serde_json::Value;

pub fn recv_msg(stream: &mut TcpStream) -> std::io::Result<Option<Value>> {
    let mut header = [0_u8; 4];
    if !read_exact_or_closed(stream, &mut header)? {
        return Ok(None);
    }
    let size = u32::from_be_bytes(header) as usize;
    let mut data = vec![0_u8; size];
    if !read_exact_or_closed(stream, &mut data)? {
        return Ok(None);
    }
    let value = serde_json::from_slice(&data)
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    Ok(Some(value))
}

pub fn send_msg(stream: &mut TcpStream, msg: &Value) -> std::io::Result<()> {
    let data = serde_json::to_vec(msg)
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    stream.write_all(&(data.len() as u32).to_be_bytes())?;
    stream.write_all(&data)?;
    stream.flush()
}

fn read_exact_or_closed(stream: &mut TcpStream, buf: &mut [u8]) -> std::io::Result<bool> {
    let mut read = 0;
    while read < buf.len() {
        let n = stream.read(&mut buf[read..])?;
        if n == 0 {
            return Ok(false);
        }
        read += n;
    }
    Ok(true)
}

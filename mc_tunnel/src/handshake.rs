// handshake.rs — Localhost handshake between MC (Python) and mc-tunnel.
//
// PROPRIETARY AND CONFIDENTIAL.
// Copyright (c) 2026 Clayrune. All rights reserved.
//
// See `02-attestation-protocol.md` §5.

use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct McSession {
    pub mc_version: String,
    pub mc_pid: u32,
    pub challenge: String,            // base64; mc signs this with device priv on demand
    pub device_pub_b64: String,
    pub username: String,
    pub enrollment_token: String,
    pub control_plane_url: String,
}

pub async fn read_secret_from_stdin() -> std::io::Result<Vec<u8>> {
    use tokio::io::{AsyncBufReadExt, BufReader};
    let stdin = tokio::io::stdin();
    let mut reader = BufReader::new(stdin);
    let mut line = String::new();
    reader.read_line(&mut line).await?;
    let trimmed = line.trim().to_string();
    if trimmed.is_empty() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "handshake secret missing on stdin",
        ));
    }
    Ok(trimmed.into_bytes())
}

pub async fn call_mc(_mc_port: u16, _secret: &[u8]) -> Result<McSession, Box<dyn std::error::Error>> {
    // TODO:
    //   GET http://127.0.0.1:{mc_port}/api/tunnel-handshake
    //     Authorization: Bearer <secret>
    //     X-MC-Tunnel-Version: env!("CARGO_PKG_VERSION")
    //   parse JSON body into McSession.
    todo!("implement /api/tunnel-handshake call")
}

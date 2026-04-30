// cloudflared.rs — Subprocess management for the bundled `cloudflared` binary.
//
// PROPRIETARY AND CONFIDENTIAL.
// Copyright (c) 2026 Clayrune. All rights reserved.
//
// We don't reimplement Cloudflare's tunnel protocol. We just run their
// official `cloudflared` binary with the tunnel token issued by the control
// plane and supervise it.

pub async fn start_or_rotate(_tunnel_token: &str) -> Result<(), Box<dyn std::error::Error>> {
    // TODO:
    //   if cloudflared subprocess not running:
    //     spawn `cloudflared tunnel run --token <token>`
    //     install crash-restart with backoff
    //   else:
    //     cloudflared can hot-reload via SIGHUP + new env var, or restart cleanly.
    //     v1 simplest: kill + respawn with new token (1-2s blip; acceptable).
    todo!("cloudflared subprocess management")
}

pub async fn stop() -> Result<(), Box<dyn std::error::Error>> {
    todo!("graceful cloudflared shutdown")
}

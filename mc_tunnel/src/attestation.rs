// attestation.rs — Build, sign, and POST attestation envelopes.
//
// PROPRIETARY AND CONFIDENTIAL.
// Copyright (c) 2026 Clayrune. All rights reserved.
//
// See `02-attestation-protocol.md` §7. This is the load-bearing module:
// every 10 minutes it produces an envelope signed by BOTH the device key
// (proxied through MC's `/api/tunnel-handshake/sign`) and the embedded
// client secret (us, locally), then exchanges it for a 15-minute Cloudflare
// tunnel token.

use crate::client_secret::{CLIENT_SECRET_KEY_ID, CLIENT_SECRET_PRIV};
use crate::handshake::McSession;

pub async fn run_loop(_session: McSession, _cp_host: Option<String>) -> Result<(), Box<dyn std::error::Error>> {
    // TODO:
    //   loop:
    //     GET /v1/nonce -> nonce, nonce_id
    //     build inner envelope (proto, device_pub, mc_version, mc_tunnel_version,
    //                            client_secret_key_id = CLIENT_SECRET_KEY_ID, etc.)
    //     canonical-json (serde_jcs) -> bytes
    //     sha256(bytes) -> envelope_canonical_sha256
    //     ask MC to sign envelope_canonical_sha256 with device priv -> signature_b64
    //     sign envelope_canonical_sha256 with CLIENT_SECRET_PRIV -> client_signature_b64
    //     POST /v1/attest { envelope, envelope_canonical_sha256, signature_b64, client_signature_b64 }
    //     on success: hand cloudflared the new token via cloudflared::rotate_token(...)
    //     sleep until next_attestation_after - 30s safety margin
    //     handle directives (force_logout / update_required / pause / notify_user)
    let _ = CLIENT_SECRET_KEY_ID;
    let _ = CLIENT_SECRET_PRIV;
    todo!("attestation loop")
}

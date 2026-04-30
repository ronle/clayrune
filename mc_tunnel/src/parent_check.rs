// parent_check.rs — Verify that mc-tunnel was spawned by MC.
//
// PROPRIETARY AND CONFIDENTIAL.
// Copyright (c) 2026 Clayrune. All rights reserved.
//
// With code-signing out of v1 scope (`05-` §1), we can no longer hash
// the parent binary against a signed manifest. We still want a basic
// "the process that spawned me has the PID it claims" check to defeat
// trivial tricks where someone tries to talk to mc-tunnel directly.
//
// Strong adversary defense comes from CLIENT_SECRET_PRIV being baked
// into this binary, not from this check.

use sysinfo::{Pid, System};
use tracing::warn;

pub async fn verify(claimed_mc_pid: u32) -> bool {
    let mut sys = System::new();
    sys.refresh_all();

    let me = std::process::id();
    let my_proc = match sys.process(Pid::from_u32(me)) {
        Some(p) => p,
        None => {
            warn!("Could not read own process info");
            return false;
        }
    };

    let actual_parent = match my_proc.parent() {
        Some(p) => p.as_u32(),
        None => {
            warn!("Own process has no parent — refusing");
            return false;
        }
    };

    if actual_parent != claimed_mc_pid {
        warn!(actual_parent, claimed_mc_pid, "Parent PID mismatch");
        return false;
    }

    true
}

// PLACEHOLDER — overwritten by build.rs at compile time.
// PROPRIETARY AND CONFIDENTIAL.
//
// This file is checked in only to keep `cargo check` / IDE features happy.
// At build time, build.rs replaces it with one containing the real
// CLIENT_SECRET_PRIV / CLIENT_SECRET_KEY_ID constants.
// See `05-build-pipeline.md` §3.1.

#![allow(dead_code)]
pub const CLIENT_SECRET_KEY_ID: &str = "DEV-NOT-FOR-RELEASE";
pub const CLIENT_SECRET_PRIV: [u8; 32] = [0u8; 32];
pub const IS_DEV_BUILD: bool = true;

# Bundled cloudflared

This directory holds a copy of Cloudflare's official `cloudflared` binary, which `mc_remote/tunnel_supervisor.py` spawns as a subprocess to run the tunnel.

## License

`cloudflared-LICENSE.txt` (Apache 2.0). Bundling is permitted.

## Currently bundled

- **Version:** 2026.3.0 (latest at the time of vendoring)
- **Source:** <https://github.com/cloudflare/cloudflared/releases/latest>
- **SHA256 (Windows amd64):** `59b12880b24af581cf5b1013db601c7d843b9b097e9c78aa5957c7f39f741885`
- **Authenticode signed by:** `CN="Cloudflare, Inc."`, issued by DigiCert Trusted G4 Code Signing RSA4096 SHA384 2021 CA1
- **Cert thumbprint:** `AB819AAE2A643DAE5A67DFA21BABC8C964F7C525`

## How to update

```powershell
# Download
Invoke-WebRequest `
  -Uri https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe `
  -OutFile mc_tunnel\bin\cloudflared.exe

# Verify Authenticode signature (must show Status: Valid, Subject contains "Cloudflare, Inc.")
Get-AuthenticodeSignature mc_tunnel\bin\cloudflared.exe | Format-List Status,SignerCertificate

# Record the new SHA in this README
(Get-FileHash mc_tunnel\bin\cloudflared.exe -Algorithm SHA256).Hash
```

For macOS/Linux releases, fetch from the matching asset in the same release and place under `mc_tunnel/bin/<platform>/cloudflared`.

## Why bundled, not auto-downloaded

See `docs/remote-access/05-build-pipeline.md` §1.5 (open-core split) and the conversation that produced this directory: bundling avoids first-run network failures, corporate-firewall friction, and an extra "what's a tunnel client?" step in the user UX.

## Where this is loaded from

`mc_remote/cloudflared.py:find_binary()` looks here **first**, before PATH. So if the binary exists in this directory, the supervisor uses it; if not, it falls back to a system-installed cloudflared.

"""
cloudflared — subprocess management for the bundled `cloudflared` binary.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

We don't reimplement Cloudflare's tunnel protocol — we just run their
official `cloudflared` binary with a tunnel token issued by the control
plane and supervise it.

Lifecycle (driven by tunnel_supervisor.py):

    cf = get()
    cf.start(token)          # spawn cloudflared with this tunnel token
    cf.is_alive()            # used by the supervisor's online-state check
    cf.swap_token(new_token) # called after each successful attestation
    cf.stop()                # graceful shutdown

Modes:

  - REAL  — runs the real `cloudflared` binary. Token must be a real
            CF-issued tunnel token; mock tokens (`MOCK_TUNNEL_TOKEN_*`)
            cause cloudflared to fail immediately.
  - MOCK  — pretends to run cloudflared. Used when MC_REMOTE_LOCAL_MOCK=1
            so the full supervision flow can be tested without a real
            CF account or binary. Always reports alive after start().

Binary discovery (REAL mode):

  1. MC_CLOUDFLARED_PATH env var (full path)
  2. <repo>/mc_tunnel/bin/cloudflared[.exe]   (bundled, future)
  3. PATH lookup for `cloudflared` / `cloudflared.exe`
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ─── Auto-download ───────────────────────────────────────────────────────────
# Self-healing fetch from CF's GitHub releases when the binary is missing.
# Triggered the first time `ensure_binary()` runs on an install that lacks a
# bundled cloudflared.exe / cloudflared (gitignored, never ships in a clone
# or via the Windows installer's git checkout). Without this, fresh installs
# hit "cloudflared binary not found" on Remote Access enable + offer no
# in-app recovery.

_CF_DOWNLOAD_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"
_DOWNLOAD_LOCK = threading.Lock()
_DOWNLOAD_STATE = {"in_progress": False, "last_error": None}


def _platform_artifact() -> Optional[tuple[str, bool]]:
    """Return (download_url, is_tarball) for the running platform, or None
    if cloudflared doesn't ship a binary for it (e.g. exotic arch). Tarball
    flag tells the downloader to extract the inner `cloudflared` file
    instead of using the response body directly — macOS releases ship as
    `.tgz`; Windows / Linux ship as raw binaries."""
    sys_name = platform.system().lower()
    arch = (platform.machine() or "").lower()
    base = _CF_DOWNLOAD_BASE
    if sys_name == "windows":
        if arch in ("amd64", "x86_64"):
            return f"{base}/cloudflared-windows-amd64.exe", False
        if arch == "arm64":
            return f"{base}/cloudflared-windows-arm64.exe", False
    elif sys_name == "darwin":
        if arch == "arm64":
            return f"{base}/cloudflared-darwin-arm64.tgz", True
        if arch in ("amd64", "x86_64"):
            return f"{base}/cloudflared-darwin-amd64.tgz", True
    elif sys_name == "linux":
        if arch in ("amd64", "x86_64"):
            return f"{base}/cloudflared-linux-amd64", False
        if arch in ("arm64", "aarch64"):
            return f"{base}/cloudflared-linux-arm64", False
    return None


def _download_target_dir() -> Path:
    """Where the auto-downloaded binary lands — same place `find_binary()`
    already checks (slot 2), so subsequent calls succeed without going back
    through the network."""
    return Path(__file__).resolve().parent.parent / "mc_tunnel" / "bin"


def _download_cloudflared() -> Optional[str]:
    """Fetch the right binary for this platform and place it in
    mc_tunnel/bin/. Returns the path on success, None on failure. Logs
    progress. Concurrent callers serialize on _DOWNLOAD_LOCK so we don't
    do parallel downloads on dashboard load + Remote Access enable."""
    with _DOWNLOAD_LOCK:
        # Re-check after acquiring the lock — another thread may have just
        # finished the download.
        existing = find_binary()
        if existing:
            return existing

        artifact = _platform_artifact()
        if artifact is None:
            log.warning("cloudflared: no published release for %s/%s",
                        platform.system(), platform.machine())
            _DOWNLOAD_STATE["last_error"] = "no published release for this platform"
            return None

        url, is_tarball = artifact
        target_dir = _download_target_dir()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.error("cloudflared: cannot create %s: %s", target_dir, e)
            _DOWNLOAD_STATE["last_error"] = f"cannot create target dir: {e}"
            return None

        target = target_dir / _exe_name()
        _DOWNLOAD_STATE["in_progress"] = True
        _DOWNLOAD_STATE["last_error"] = None
        try:
            log.info("cloudflared: downloading from %s …", url)
            # Stream to a sibling temp file so a partial download never
            # poses as a real binary.
            with tempfile.NamedTemporaryFile(
                prefix="cloudflared-", suffix=".part",
                dir=str(target_dir), delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                with urllib.request.urlopen(url, timeout=120) as resp:
                    shutil.copyfileobj(resp, tmp)

            if is_tarball:
                # macOS releases are gzipped tar archives containing a
                # single `cloudflared` Mach-O binary.
                extracted = target_dir / "_cf_extract"
                shutil.rmtree(extracted, ignore_errors=True)
                extracted.mkdir(parents=True)
                with tarfile.open(tmp_path, "r:gz") as tf:
                    tf.extractall(extracted)
                # The binary may be at the root or nested in a subdir.
                inner = next(extracted.rglob("cloudflared"), None)
                if inner is None:
                    raise RuntimeError("cloudflared not found inside tarball")
                shutil.move(str(inner), str(target))
                shutil.rmtree(extracted, ignore_errors=True)
                tmp_path.unlink(missing_ok=True)
            else:
                # Raw binary — just rename into place atomically.
                tmp_path.replace(target)

            if sys.platform != "win32":
                # +x for Unix targets so subprocess.Popen can spawn it.
                try:
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except Exception as e:
                    log.warning("cloudflared: chmod +x failed: %s", e)

            log.info("cloudflared: downloaded to %s (%d bytes)",
                     target, target.stat().st_size)
            return str(target)
        except urllib.error.URLError as e:
            log.error("cloudflared: download failed (network): %s", e)
            _DOWNLOAD_STATE["last_error"] = f"network error: {e.reason if hasattr(e, 'reason') else e}"
            return None
        except Exception as e:
            log.error("cloudflared: download failed: %s", e)
            _DOWNLOAD_STATE["last_error"] = f"download failed: {e}"
            return None
        finally:
            _DOWNLOAD_STATE["in_progress"] = False


def ensure_binary() -> Optional[str]:
    """Find the cloudflared binary, downloading it if missing. Returns the
    path or None if download is impossible (unsupported platform, network
    failure). Use this instead of `find_binary()` from any hot path that
    actually intends to spawn the subprocess."""
    p = find_binary()
    if p:
        return p
    return _download_cloudflared()


def download_state() -> dict:
    """Snapshot of the auto-download lifecycle for surface in UI/logs."""
    return dict(_DOWNLOAD_STATE)


class CloudflaredError(RuntimeError):
    """Raised when cloudflared can't start (binary missing, etc.)."""


# ─── Binary discovery ────────────────────────────────────────────────────────


def _exe_name() -> str:
    return "cloudflared.exe" if sys.platform == "win32" else "cloudflared"


def find_binary() -> Optional[str]:
    """Locate the cloudflared binary, or None if not available."""
    # 1. Explicit env override
    p = os.environ.get("MC_CLOUDFLARED_PATH")
    if p and Path(p).is_file():
        return p

    # 2. Bundled location alongside mc_tunnel (when ship-bundled)
    here = Path(__file__).resolve().parent.parent
    bundled = here / "mc_tunnel" / "bin" / _exe_name()
    if bundled.is_file():
        return str(bundled)

    # 3. PATH lookup
    p = shutil.which(_exe_name())
    if p:
        return p
    return None


# ─── Real cloudflared subprocess ─────────────────────────────────────────────


class CloudflaredProcess:
    """Manages a single live cloudflared subprocess."""

    def __init__(self, *, binary_path: Optional[str] = None) -> None:
        # `ensure_binary` triggers an auto-download (CF GitHub releases) if
        # the binary isn't already on disk. Falls back to `None` only when
        # network is unreachable / platform unsupported — same failure mode
        # `find_binary` had before, just with a self-healing first attempt.
        self._binary = binary_path or ensure_binary()
        self._proc: Optional[subprocess.Popen] = None
        self._token: Optional[str] = None
        self._started_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._lock = threading.RLock()
        self._reader_thread: Optional[threading.Thread] = None

    # ─── Public API ───────────────────────────────────────────────────────

    def start(self, token: str) -> None:
        """Spawn cloudflared with `token`. If already running with the same
        token, no-op. With a different token, restarts."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                if self._token == token:
                    return  # same token, already running
                self._stop_locked()  # different token → restart

            if not self._binary:
                self._last_error = "cloudflared binary not found"
                raise CloudflaredError(
                    "cloudflared binary not found. Set MC_CLOUDFLARED_PATH or install cloudflared."
                )

            log.info("starting cloudflared (%s)", self._binary)
            try:
                self._proc = subprocess.Popen(
                    [self._binary, "tunnel", "--no-autoupdate", "run", "--token", token],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
                )
            except FileNotFoundError as e:
                self._last_error = f"cloudflared launch failed: {e}"
                raise CloudflaredError(str(e)) from e

            self._token = token
            self._started_at = time.time()
            self._last_error = None
            self._reader_thread = threading.Thread(
                target=self._read_output_loop, daemon=True, name="cloudflared-reader",
            )
            self._reader_thread.start()

    def is_alive(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def stop(self, *, timeout: float = 3.0) -> None:
        with self._lock:
            self._stop_locked(timeout=timeout)

    def swap_token(self, new_token: str) -> None:
        """Restart cloudflared with a new token. Brief connectivity blip
        (~1-3s) is acceptable for a routine token rotation every 10 min."""
        self.start(new_token)

    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def lifetime_seconds(self) -> Optional[float]:
        with self._lock:
            return None if self._started_at is None else time.time() - self._started_at

    # ─── Internals ────────────────────────────────────────────────────────

    def _stop_locked(self, *, timeout: float = 3.0) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    log.warning("cloudflared did not exit gracefully — killing")
                    proc.kill()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception as e:
            log.warning("cloudflared stop raised: %s", e)
        finally:
            self._proc = None
            self._token = None
            self._started_at = None
            self._reader_thread = None

    def _read_output_loop(self) -> None:
        """Pipe cloudflared's combined stdout/stderr into MC's log.

        Filters out HTTP/2 stream-cancel "ERR" lines (cloudflared reports
        normal client-disconnect with `error code 0` as ERR). These are
        protocol-clean and very noisy with SSE-heavy origins like MC.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return

        # Lines that look like errors but are actually protocol-normal.
        # See conversation 2026-04-29 for the analysis.
        BENIGN_PATTERNS = (
            "canceled by remote with error code 0",
            "stream error: stream ID",
            "client disconnected",
        )

        try:
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                lowered = line.lower()
                is_benign = any(p in lowered for p in BENIGN_PATTERNS)
                log.info("[cloudflared] %s", line)
                if not is_benign and any(k in lowered for k in ("error", "fatal", "failed to")):
                    with self._lock:
                        self._last_error = line[:200]
        except Exception as e:
            log.warning("cloudflared stdout reader exited: %s", e)


# ─── Mock cloudflared (used in MC_REMOTE_LOCAL_MOCK mode) ───────────────────


class MockCloudflaredProcess:
    """Pretends to run cloudflared. Always alive after start(), idempotent.

    Used when MC_REMOTE_LOCAL_MOCK=1 so the entire supervision pipeline can
    be exercised without a real binary or CF account.
    """

    def __init__(self) -> None:
        self._running = False
        self._token: Optional[str] = None
        self._started_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._lock = threading.Lock()

    def start(self, token: str) -> None:
        with self._lock:
            if self._running and self._token == token:
                return
            log.info("[mock cloudflared] start with token %s...", token[:24])
            self._running = True
            self._token = token
            self._started_at = time.time()
            self._last_error = None

    def is_alive(self) -> bool:
        with self._lock:
            return self._running

    def stop(self, *, timeout: float = 3.0) -> None:
        with self._lock:
            if self._running:
                log.info("[mock cloudflared] stop")
            self._running = False
            self._token = None
            self._started_at = None

    def swap_token(self, new_token: str) -> None:
        self.start(new_token)

    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def lifetime_seconds(self) -> Optional[float]:
        with self._lock:
            return None if self._started_at is None else time.time() - self._started_at

    # Test-only helpers — let smoke tests simulate a crash
    def _force_dead(self) -> None:
        with self._lock:
            self._running = False
            self._last_error = "simulated crash"

    def _force_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = msg


# ─── Factory ────────────────────────────────────────────────────────────────


_instance: Optional[object] = None
_instance_lock = threading.Lock()


def get():
    """Return the singleton cloudflared manager (real or mock per env)."""
    global _instance
    with _instance_lock:
        if _instance is None:
            if os.environ.get("MC_REMOTE_LOCAL_MOCK") == "1":
                _instance = MockCloudflaredProcess()
                log.info("cloudflared: mock mode")
            else:
                _instance = CloudflaredProcess()
                bin_loc = getattr(_instance, "_binary", None)
                log.info("cloudflared: real mode (binary=%s)",
                         bin_loc or "NOT FOUND")
        return _instance


def reset_for_tests() -> None:
    """Test-only: drop the singleton so the next get() builds fresh."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            try:
                _instance.stop()
            except Exception:
                pass
        _instance = None

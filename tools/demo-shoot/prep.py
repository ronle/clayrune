#!/usr/bin/env python3
"""Get the demo instance camera-ready. Run this immediately before each take.

    python tools/demo-shoot/prep.py

Brings up an ISOLATED Clayrune (its own port, its own MC_DATA_DIR) with fake
projects, then starts three real Claude agents so the grid is genuinely live when
you hit record. Nothing here can see or touch your real projects.

Why a script and not a checklist: the agents finish in a couple of minutes, so
they have to be started fresh for every take. Doing that by hand between takes is
how a shoot dies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PORT = 5200
BASE = f"http://localhost:{PORT}"
DATA_DIR = ROOT / "_scratch" / "demo-inst2"
REPOS = ROOT / "_scratch" / "demo2"

PROJECTS = [
    ("orchard", "Orchard", "Marketing site"),
    ("pathfinder", "Pathfinder", "API gateway"),
    ("lantern", "Lantern", "Mobile companion app"),
    ("almanac", "Almanac", "Weekly data digest"),
]

# Long, chatty tasks: they must still be streaming when the camera rolls.
# Real work on throwaway repos — nothing here is staged.
TAKE_TASKS = [
    ("orchard",
     "Add a responsive footer with social links. Narrate your work as you go: read "
     "the existing files and describe what you find, explain your plan step by step, "
     "then implement it in src/header.html and src/styles.css. Explain every edit."),
    ("pathfinder",
     "Add structured request-logging middleware to src/routes.js (method, path, status, "
     "duration). Do NOT ask questions — make sensible choices and state them. Read the "
     "file, describe it, discuss the trade-offs at length, then implement."),
    ("lantern",
     "Write a detailed plan for adding an offline cache to the feed. Discuss every "
     "trade-off at length. Do not edit any files."),
]


def post(path: str, payload: dict, timeout: int = 30):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def up() -> bool:
    try:
        urllib.request.urlopen(f"{BASE}/api/system/heartbeat", timeout=3)
        return True
    except Exception:
        return False


def lan_ip() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "
             "'192.168.*' -or $_.IPAddress -like '10.*' } | Select-Object -First 1 "
             "-ExpandProperty IPAddress)"],
            capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip() or "<your-LAN-IP>"
    except Exception:
        return "<your-LAN-IP>"


def start_instance() -> None:
    if up():
        print(f"  demo instance already up on :{PORT}")
        return
    print(f"  starting demo instance on :{PORT} …")
    env = dict(os.environ, MC_PORT=str(PORT), MC_DATA_DIR=str(DATA_DIR))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = open(DATA_DIR / "server.log", "ab")
    subprocess.Popen([sys.executable, str(ROOT / "server.py")],
                     cwd=str(ROOT), env=env, stdout=log, stderr=log)
    for _ in range(30):
        time.sleep(1)
        if up():
            print("  up.")
            return
    sys.exit("  FAILED: demo instance did not come up. Check _scratch/demo-inst2/server.log")


def ensure_projects() -> None:
    for pid, name, summary in PROJECTS:
        try:
            post(f"/api/project/{pid}", {
                "id": pid, "name": name, "summary": summary,
                "project_path": str(REPOS / pid), "status": "active",
            })
        except urllib.error.URLError as e:
            print(f"  ! could not create {pid}: {e}")


def reset_repos() -> None:
    """Roll the throwaway repos back to their committed baseline.

    Without this the agent finds its own leftovers from the last take and says so
    on camera ("this looks like it's already been done") — which is exactly the
    kind of thing that reads as fake.
    """
    for pid, _, _ in PROJECTS:
        d = REPOS / pid
        if not (d / ".git").is_dir():
            continue
        subprocess.run(["git", "checkout", "--", "."], cwd=str(d), capture_output=True)
        subprocess.run(["git", "clean", "-fdq"], cwd=str(d), capture_output=True)
    print("  repos reset to baseline")


def dispatch() -> None:
    for pid, task in TAKE_TASKS:
        try:
            ok = post(f"/api/project/{pid}/agent/dispatch", {"task": task}).get("ok")
            print(f"  dispatched {pid:<11} {'ok' if ok else 'FAILED'}")
        except Exception as e:
            print(f"  ! dispatch {pid} failed: {e}")


def main() -> None:
    print("Clayrune — demo shoot prep\n")
    start_instance()
    ensure_projects()
    reset_repos()
    print("\n  starting three real agents …")
    dispatch()

    print("\n  waiting for them to come up as IN PROGRESS …")
    for _ in range(20):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{BASE}/api/projects", timeout=5) as r:
                ps = json.loads(r.read().decode())
            live = [p["name"] for p in ps if p.get("live_agent")]
            if len(live) >= 3:
                print(f"  LIVE: {', '.join(live)}")
                break
        except Exception:
            pass
    else:
        print("  (some agents may still be spinning up — check the grid)")

    # ASCII only: the Windows console is cp1252 and box-drawing characters raise
    # UnicodeEncodeError, which would kill the script at the last line.
    ip = lan_ip()
    print(f"""
{'-' * 62}
  READY. Roll camera within ~2 minutes - the agents finish.

  Desktop :  http://localhost:{PORT}
  PHONE   :  http://{ip}:{PORT}      <- same WiFi. NOT the tunnel.

  The tunnel points at your REAL instance (:5199) and would put
  your real project names on camera. Always use the IP above.
{'-' * 62}
""")


if __name__ == "__main__":
    main()

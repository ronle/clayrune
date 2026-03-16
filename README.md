# Mission Control

A multi-project management dashboard for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents. Manage, dispatch, and monitor AI coding agents across all your projects from a single interface.

## What It Does

Mission Control gives you a centralized dashboard to:

- **Manage multiple projects** with status tracking, descriptions, and domains
- **Dispatch Claude Code agents** to work on tasks across any project
- **ReUtilize previous chat sessions under same project for reducing overall token usage
- **Monitor agent activity in real-time** via streaming output
- **Maintain project backlogs** with priorities, drag-and-drop ordering, and file attachments
- **Review agent session history** and completed work
- **Open multiple project windows** simultaneously (multi-modal windowing system)
- **View agent plans** in a dedicated wide-format viewer
- **Share baseline rules** across all projects (SHARED_RULES.md)

## Quick Start (Windows)

1. **Download** `MissionControl-Windows.zip` from [Releases](https://github.com/ronle/mission-control/releases/latest)
2. **Unzip** anywhere (e.g. your Desktop or Documents)
3. **Double-click** `MissionControl.exe`

That's it. A native window opens with the full dashboard. On first launch the app will attempt to install the Claude CLI automatically if it's not already on your system.

The web interface is also accessible at `http://localhost:5199` while the app is running.

## Prerequisites (from source only)

If you prefer running from source instead of the prebuilt exe:

- **Python 3.9+** — [Download](https://www.python.org/downloads/)
- **Claude CLI** — [Installation guide](https://docs.anthropic.com/en/docs/claude-code/getting-started)

## Running from Source

### Option A: Desktop Window

```bash
git clone https://github.com/ronle/mission-control.git
cd mission-control
pip install -r requirements.txt
python app.py
```

Opens a native window with the dashboard. Flask server runs in the background.

### Option B: Browser Only

```bash
git clone https://github.com/ronle/mission-control.git
cd mission-control
pip install flask
python server.py
# Open http://localhost:5199 in your browser
```

### Option C: Automated Setup

**Windows:**
```
install.bat
```

**macOS / Linux:**
```bash
chmod +x install.sh
./install.sh
```

## Configuration

On first run, a `config.json` file is created with defaults:

```json
{
  "port": 5199,
  "shared_rules_path": "data/SHARED_RULES.md",
  "projects_base": "/home/you"
}
```

| Setting | Description | Default |
|---------|-------------|---------|
| `port` | Server port | `5199` |
| `shared_rules_path` | Path to shared rules file (injected into all agent prompts) | `data/SHARED_RULES.md` |
| `projects_base` | Base directory for project path validation | User home directory |

You can also set the port via environment variable: `MC_PORT=8080 python server.py`

## Features

### Project Dashboard
- Tile-based overview of all projects with status indicators
- Drag-and-drop reordering
- Domain categorization and color coding
- Activity stream across all projects

### Agent Management
- Dispatch tasks to Claude Code agents with one click
- Real-time streaming output with syntax highlighting
- Send follow-up messages to running agents
- Multiple concurrent agent sessions per project
- Stop/resume agent sessions
- Paste screenshots directly into agent prompts

### Backlog
- Per-project task backlog with priorities (low/normal/high/critical)
- Drag-and-drop reordering
- File attachments with drag-and-drop upload
- Mark items done/reopen

### Plan Viewer
- Agent plans open in a dedicated wider window for easier reading
- Auto-detection of plan mode output
- Pop-out button for viewing any agent output in a larger window
- Ctrl+scroll zoom support

### Multi-Window System
- Open multiple project modals simultaneously
- Drag, resize, minimize, and restore windows
- Keyboard navigation (Escape to close)
- Minimized tray at bottom of screen

## Architecture

```
mission-control/
  app.py                 Desktop entry point (pywebview + Flask)
  server.py              Flask backend (API + static serving)
  static/
    index.html           Single-page app (HTML + CSS + JS, no build step)
  data/
    projects/            Project JSON files (auto-created)
    uploads/             File attachments
  config.json            User configuration (auto-created, gitignored)
  build.spec             PyInstaller build spec
  build.bat              Build automation script
```

- **Backend**: Python Flask server on configurable port (default 5199)
- **Frontend**: Vanilla HTML/CSS/JS single-page app (no framework, no build step)
- **Data**: JSON files on disk (no database required)
- **Agent**: Spawns `claude` CLI as subprocess with streaming JSON output
- **Desktop**: Native window via pywebview (WebView2); prebuilt exe available in Releases

## Contributing

Contributions are welcome! Here's how to get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Test locally: `python server.py` and verify in browser
5. Submit a pull request

### Development Notes

- The entire frontend is in a single `static/index.html` file — no build step needed
- The server is a single `server.py` file with Flask
- Data is stored as JSON files in `data/projects/`
- Agent sessions use Server-Sent Events (SSE) for real-time streaming
- The `claude` CLI is invoked via `subprocess.Popen` with `--output-format stream-json`

## License

[MIT](LICENSE)

# Clayrune — Claude Code project notes

## Video attachments — use the frame extractor

Claude (this model) doesn't read videos natively. When the user attaches an
`.mp4` / `.mov` / `.webm` / `.avi` / `.mkv` file in this repo (typically under
`data/uploads/agent_*.mp4`), do this **before** trying to describe it:

```bash
tools/extract-frames.sh <path-to-video>
```

That writes `<basename>_frames/frame_001.png ... frame_NNN.png` next to the
video. Read those PNGs with the Read tool to actually see the content.

Defaults: 2 fps, capped at 24 frames. Override for longer / more detailed
clips: `tools/extract-frames.sh video.mp4 4 48` (4 fps, up to 48 frames).

ffmpeg must be installed (`winget install Gyan.FFmpeg` / `apt install ffmpeg`
/ `brew install ffmpeg`). The script tells the user how to install if missing.

## Live test environments

Two VMs are kept clean for end-to-end install testing:
- Windows 11 Home VM
- Ubuntu 22.04 VM

Both validated `c34cf44` clean. Re-test on a fresh snapshot if you change
anything in `installer/`.

## Skills (Anthropic-format) — management surface

Clayrune ships a Skills surface (sidebar entry above Backlog, project-modal
three-dot menu entry) that manages skills CC reads from `~/.claude/skills/`
and `<project_path>/.claude/skills/`. Five built-ins ship under
`data/skills/builtin/` and install once on startup with checksum-based
update preservation (`skills.install_builtins`). User edits to a managed
built-in are preserved across updates.

To add a new built-in: drop a folder under `data/skills/builtin/<name>/`
with a `SKILL.md` (and optional `scripts/`, `references/`). On next MC
startup `_install_builtin_skills()` will install it. Bump the source file
to push an update — checksum drift triggers re-install for users who
haven't modified their copy.

Backend: `skills.py` module + `# ── Skills endpoints` section in
`server.py`. Frontend: `// ── Skills (global + per-project ...)` section
in `static/index.html`. Architecture and rollback recipe in CHANGELOG
`[2026-05-10]`.

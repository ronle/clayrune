# Mission Control — Claude Code project notes

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

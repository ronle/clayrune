#!/usr/bin/env bash
# extract-frames.sh — turn a video into a small set of PNG frames an LLM
# with vision can read. Built so Claude Code (which can't read videos
# natively) gets a useful fallback whenever the user shares a clip.
#
# Usage:
#   tools/extract-frames.sh <video> [fps] [max_frames]
#
# Defaults:
#   fps         = 2     (samples per second of source video)
#   max_frames  = 24    (hard cap to keep model context manageable)
#
# Output:
#   <video-basename>_frames/frame_001.png ... frame_NNN.png
#   Prints the directory + frame count + paths so the caller can grep.
#
# Why these defaults: 2 fps for ~12 seconds of footage gives 24 frames,
# which is roughly the right amount of context for "describe this
# animation technique" without overwhelming. For longer clips, the
# max_frames cap kicks in and ffmpeg subsamples evenly across the
# whole duration.

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <video> [fps=2] [max_frames=24]" >&2
    exit 2
fi

VIDEO="$1"
FPS="${2:-2}"
MAX_FRAMES="${3:-24}"

if [ ! -f "$VIDEO" ]; then
    echo "ERROR: file not found: $VIDEO" >&2
    exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ERROR: ffmpeg not on PATH. Install it:" >&2
    echo "  Windows: winget install Gyan.FFmpeg" >&2
    echo "  Ubuntu:  sudo apt install ffmpeg" >&2
    echo "  macOS:   brew install ffmpeg" >&2
    exit 3
fi

# Probe duration so we can compute an even-spaced sample if max_frames
# would clip the naive fps-based extraction.
DURATION=$(ffprobe -v quiet -of csv=p=0 -show_entries format=duration "$VIDEO" 2>/dev/null || echo "0")
DURATION="${DURATION%.*}"  # integer seconds
[ -z "$DURATION" ] || [ "$DURATION" = "0" ] && DURATION=1

NAIVE_COUNT=$(awk -v d="$DURATION" -v f="$FPS" 'BEGIN{printf "%d", d * f}')

# If naive fps would exceed max_frames, switch to even sampling: pick
# max_frames from across the whole duration. Otherwise use the requested fps.
if [ "$NAIVE_COUNT" -gt "$MAX_FRAMES" ]; then
    EFFECTIVE_FPS=$(awk -v m="$MAX_FRAMES" -v d="$DURATION" 'BEGIN{printf "%.4f", m / d}')
else
    EFFECTIVE_FPS="$FPS"
fi

# Output dir alongside the video file: <basename>_frames/
DIR=$(dirname "$VIDEO")
BASE=$(basename "$VIDEO")
STEM="${BASE%.*}"
OUT_DIR="$DIR/${STEM}_frames"

# Clean previous extraction so frame counts don't get confused on re-runs.
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

ffmpeg -i "$VIDEO" \
    -vf "fps=${EFFECTIVE_FPS}" \
    -frames:v "$MAX_FRAMES" \
    "$OUT_DIR/frame_%03d.png" \
    -hide_banner -loglevel error -y

COUNT=$(ls -1 "$OUT_DIR"/frame_*.png 2>/dev/null | wc -l | tr -d ' ')

echo "Extracted $COUNT frames from $VIDEO"
echo "  duration: ${DURATION}s, effective fps: $EFFECTIVE_FPS"
echo "  output: $OUT_DIR/"
echo ""
echo "Frames (read these with the Read tool):"
ls -1 "$OUT_DIR"/frame_*.png

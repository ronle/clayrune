#!/usr/bin/env bash
# sheet-to-frames.sh — slice a grid image (e.g. 2x2 character-state sheet
# from Gemini / DALL-E) into separate frame files. Companion to
# frames-to-animation.sh; chain them to go from sheet -> animated webp.
#
# Usage:
#   tools/sheet-to-frames.sh <sheet> <cols>x<rows> [output-dir]
#
# Examples:
#   tools/sheet-to-frames.sh thinking-sheet.webp 2x2
#       -> thinking-sheet_frames/frame_1.png ... frame_4.png
#   tools/sheet-to-frames.sh idle.webp 4x1 assets/idle-frames
#       -> assets/idle-frames/frame_1.png ... frame_4.png
#
# Frames are numbered LEFT-TO-RIGHT, TOP-TO-BOTTOM (reading order). Output
# is always PNG so the alpha channel is preserved cleanly through the
# chain — re-encode to WebP at the animation step.

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <sheet> <cols>x<rows> [output-dir]" >&2
    exit 2
fi

SHEET="$1"
GRID="$2"
OUT_DIR="${3:-}"

if [ ! -f "$SHEET" ]; then
    echo "ERROR: sheet not found: $SHEET" >&2
    exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ERROR: ffmpeg not on PATH." >&2
    exit 3
fi

# Parse <cols>x<rows>
COLS="${GRID%x*}"
ROWS="${GRID#*x}"
case "$COLS" in ''|*[!0-9]*) echo "ERROR: bad cols in '$GRID'" >&2; exit 2 ;; esac
case "$ROWS" in ''|*[!0-9]*) echo "ERROR: bad rows in '$GRID'" >&2; exit 2 ;; esac

# Default output: <sheet-stem>_frames/ next to the source
if [ -z "$OUT_DIR" ]; then
    DIR=$(dirname "$SHEET")
    BASE=$(basename "$SHEET")
    STEM="${BASE%.*}"
    OUT_DIR="$DIR/${STEM}_frames"
fi

# Wipe stale output so frame numbering doesn't collide on re-run.
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

TOTAL=$((COLS * ROWS))
n=0
for r in $(seq 0 $((ROWS - 1))); do
    for c in $(seq 0 $((COLS - 1))); do
        n=$((n + 1))
        # crop=W:H:X:Y in ffmpeg's filter syntax. iw/cols + ih/rows give the
        # cell size; iw*c/cols + ih*r/rows give the top-left of cell (c, r).
        ffmpeg -y -i "$SHEET" \
            -vf "crop=iw/${COLS}:ih/${ROWS}:iw*${c}/${COLS}:ih*${r}/${ROWS}" \
            "$OUT_DIR/frame_${n}.png" \
            -hide_banner -loglevel error
    done
done

echo "Sliced $TOTAL frames from $SHEET ($GRID grid)"
echo "  output: $OUT_DIR/"
ls -1 "$OUT_DIR"/frame_*.png

#!/usr/bin/env bash
# frames-to-animation.sh — stitch a sequence of still images into a looping
# animated WebP (or GIF / APNG). Companion to extract-frames.sh: that one
# takes video to stills, this one takes stills to a single animated file.
#
# Built for the case where an AI image tool (Gemini, DALL-E, etc.) gave you
# a set of keyframes for a mascot state but can't produce an animated output.
#
# Usage:
#   tools/frames-to-animation.sh [-d MS] [-f FORMAT] [-o OUT] FRAME [FRAME ...]
#
# Options:
#   -d MS       milliseconds per frame (default 250 = 4 fps loop feel)
#   -f FORMAT   webp | gif | apng  (default webp)
#   -o OUT      output path (default: <first-frame-stem>.<format>)
#
# Examples:
#   tools/frames-to-animation.sh thinking_1.png thinking_2.png thinking_3.png thinking_4.png
#       -> thinking_1.webp, 4 frames at 250ms each, loops forever
#
#   tools/frames-to-animation.sh -d 400 -o assets/claydo-thinking.webp \
#       data/uploads/think_*.png
#       -> slower loop, custom output
#
#   tools/frames-to-animation.sh -f gif -o claydo.gif frame_*.png
#       -> GIF instead of WebP (bigger, but maximum compatibility)

set -euo pipefail

DURATION_MS=250
FORMAT="webp"
OUT=""

while getopts ":d:f:o:" opt; do
    case "$opt" in
        d) DURATION_MS="$OPTARG" ;;
        f) FORMAT="$OPTARG" ;;
        o) OUT="$OPTARG" ;;
        \?) echo "Unknown option: -$OPTARG" >&2; exit 2 ;;
    esac
done
shift $((OPTIND - 1))

if [ $# -lt 2 ]; then
    echo "Usage: $0 [-d MS] [-f FORMAT] [-o OUT] FRAME [FRAME ...]" >&2
    echo "Need at least 2 frames to make an animation." >&2
    exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ERROR: ffmpeg not on PATH. Install it:" >&2
    echo "  Windows: winget install Gyan.FFmpeg" >&2
    echo "  Ubuntu:  sudo apt install ffmpeg" >&2
    echo "  macOS:   brew install ffmpeg" >&2
    exit 3
fi

# Validate inputs exist
for f in "$@"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: frame not found: $f" >&2
        exit 2
    fi
done

# Default output: first-frame stem + format extension
if [ -z "$OUT" ]; then
    FIRST="$1"
    DIR=$(dirname "$FIRST")
    BASE=$(basename "$FIRST")
    STEM="${BASE%.*}"
    OUT="$DIR/${STEM}.${FORMAT}"
fi

# Compute fps from milliseconds-per-frame. ffmpeg expects frame rate, not
# frame duration. fps = 1000 / duration_ms.
FPS=$(awk -v d="$DURATION_MS" 'BEGIN{printf "%.4f", 1000.0 / d}')

# Build a temp concat list. Using `concat` demuxer rather than glob/pattern
# so the user can pass arbitrary filenames in any order without renaming.
CONCAT_LIST=$(mktemp --suffix=.txt 2>/dev/null || mktemp -t ftal)
trap 'rm -f "$CONCAT_LIST"' EXIT

for f in "$@"; do
    # Resolve to absolute path so the concat demuxer doesn't get confused by
    # cwd changes mid-pipeline. Two-step: first resolve to absolute (POSIX
    # form OK), then if we're on Git Bash / MSYS / Cygwin, translate to a
    # Windows path so the native ffmpeg.exe accepts it. cygpath -w on a
    # /c/... path returns C:\\..., which ffmpeg parses correctly. On Linux /
    # macOS the cygpath branch is skipped.
    if command -v realpath >/dev/null 2>&1; then
        REAL=$(realpath "$f")
    else
        REAL="$(cd "$(dirname "$f")" && pwd)/$(basename "$f")"
    fi
    if command -v cygpath >/dev/null 2>&1; then
        ABS=$(cygpath -w "$REAL")
    else
        ABS="$REAL"
    fi
    # Each frame held for DURATION_MS, encoded as a duration directive.
    # The final entry needs to be repeated without a duration to stick on
    # screen long enough for ffmpeg to finalize -- standard concat-demuxer trick.
    DURATION_S=$(awk -v d="$DURATION_MS" 'BEGIN{printf "%.4f", d / 1000.0}')
    printf "file '%s'\nduration %s\n" "$ABS" "$DURATION_S" >> "$CONCAT_LIST"
done
# Re-append the last frame without a duration to satisfy ffmpeg's concat
# demuxer (it ignores duration on the very last entry otherwise).
LAST_ABS=$(tail -2 "$CONCAT_LIST" | head -1 | sed "s/^file '//; s/'$//")
printf "file '%s'\n" "$LAST_ABS" >> "$CONCAT_LIST"

# Encode per format. Each branch tuned for visual quality vs file size at
# small mascot sizes (64-256 px square).
case "$FORMAT" in
    webp)
        # libwebp animated. -loop 0 = infinite. -lossless 0 + -q:v 75 is a
        # good size/quality knee for a 128x128 character animation.
        ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" \
            -loop 0 -vcodec libwebp -lossless 0 -q:v 75 \
            -preset default -an \
            "$OUT" -hide_banner -loglevel error
        ;;
    apng)
        # APNG: lossless, full alpha, supported in all modern browsers.
        # Bigger than WebP but cleaner edges on hard transparency.
        ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" \
            -plays 0 -f apng \
            "$OUT" -hide_banner -loglevel error
        ;;
    gif)
        # GIF: 256-color palette. Use a per-clip palettegen + paletteuse so
        # we don't get the default-palette banding ffmpeg gives by default.
        PALETTE=$(mktemp --suffix=.png 2>/dev/null || mktemp -t fta_pal)
        trap 'rm -f "$CONCAT_LIST" "$PALETTE"' EXIT
        ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" \
            -vf "palettegen=stats_mode=diff" \
            "$PALETTE" -hide_banner -loglevel error
        ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" -i "$PALETTE" \
            -lavfi "[0:v][1:v]paletteuse=dither=bayer:bayer_scale=3" \
            -loop 0 \
            "$OUT" -hide_banner -loglevel error
        ;;
    *)
        echo "ERROR: unknown format '$FORMAT' (try webp / apng / gif)" >&2
        exit 2
        ;;
esac

# Probe the result for a sanity report.
SIZE=$(wc -c < "$OUT" | tr -d ' ')
KB=$(awk -v b="$SIZE" 'BEGIN{printf "%.1f", b / 1024.0}')
echo "Created: $OUT"
echo "  format:    $FORMAT"
echo "  frames:    $#"
echo "  per-frame: ${DURATION_MS}ms"
echo "  size:      ${KB} KB"
echo ""
echo "Drop into static/index.html:"
echo "  <img src=\"/$OUT\" alt=\"Claydo state\">"

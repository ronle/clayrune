#!/bin/sh
# Install Clayrune's committed git hooks into this clone's active hooks dir.
# Idempotent — run once after cloning (and again after a hook is updated).
# Copies rather than redirecting core.hooksPath, so it respects whatever the
# clone already has configured.
set -e

root=$(git rev-parse --show-toplevel)
hooks=$(git -C "$root" rev-parse --git-path hooks)

# git-path may be relative to the repo root — resolve it.
case "$hooks" in
  /*|[A-Za-z]:*) : ;;              # already absolute (POSIX or Windows drive)
  *) hooks="$root/$hooks" ;;
esac

mkdir -p "$hooks"
cp "$root/tools/git-hooks/pre-push" "$hooks/pre-push"
chmod +x "$hooks/pre-push" 2>/dev/null || true

echo "Installed pre-push guardrail -> $hooks/pre-push"

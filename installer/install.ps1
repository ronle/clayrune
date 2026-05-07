# Clayrune installer bootstrap (Windows)
#
# Usage (in PowerShell):
#   iwr https://clayrune.io/install.ps1 -useb | iex
#
# What this script does:
#   1. Verifies Claude CLI is installed (or installs it via npm; falls back to
#      winget Node.js + npm if npm is missing).
#   2. Fetches the install prompt from clayrune.io.
#   3. Discloses what is about to happen, with a short Ctrl-C abort window.
#   4. Pipes the prompt into `claude --dangerously-skip-permissions`.
#
# After authorization, Claude itself executes the install — clones the repo,
# installs Python and Node deps, creates a Desktop / Start Menu shortcut,
# and opens the app in the user's browser.
#
# Read the install prompt before running:
#   iwr https://clayrune.io/install-prompt.md -useb | Select-Object -ExpandProperty Content
#
# Override URLs (for testing):
#   $env:CLAYRUNE_PROMPT_URL = '...'
#   $env:CLAYRUNE_NO_CONFIRM = '1'   # skip the 5-second abort window

$ErrorActionPreference = 'Stop'

$PromptUrl = if ($env:CLAYRUNE_PROMPT_URL) { $env:CLAYRUNE_PROMPT_URL } `
             else { 'https://clayrune.io/install-prompt.md' }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

Write-Host '======================================' -ForegroundColor Cyan
Write-Host '  Clayrune Installer' -ForegroundColor White
Write-Host '======================================'
Write-Host ''

# ── Step 1: Claude CLI present? ────────────────────────────────────────────
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host 'Claude CLI not found. Attempting to install...' -ForegroundColor Yellow
    Write-Host ''

    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host 'Trying npm install -g @anthropic-ai/claude-code'
        try {
            npm install -g '@anthropic-ai/claude-code'
        } catch {
            Write-Host ''
            Write-Host 'npm install failed.' -ForegroundColor Red
            Write-Host "Manual install: https://docs.anthropic.com/claude-code"
            exit 1
        }
    } elseif (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'npm not found. Installing Node.js via winget first...'
        try {
            winget install --id OpenJS.NodeJS.LTS -e --silent `
                --accept-source-agreements --accept-package-agreements
        } catch {
            Write-Host 'winget install Node.js failed.' -ForegroundColor Red
            Write-Host 'Install Node.js manually from https://nodejs.org and re-run.'
            exit 1
        }
        Refresh-Path
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            Write-Host 'Node installed but npm not on PATH.' -ForegroundColor Red
            Write-Host 'Open a new PowerShell window and re-run this installer.'
            exit 1
        }
        Write-Host 'Now installing Claude CLI via npm...'
        npm install -g '@anthropic-ai/claude-code'
    } else {
        Write-Host 'Neither npm nor winget found — cannot auto-install Claude CLI.' `
                   -ForegroundColor Red
        Write-Host 'Install it manually first:'
        Write-Host '  https://docs.anthropic.com/claude-code'
        Write-Host 'Then re-run this installer.'
        exit 1
    }

    Refresh-Path

    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host 'Claude CLI installed but not on PATH.' -ForegroundColor Yellow
        Write-Host 'Open a new PowerShell window and re-run:'
        Write-Host '  iwr https://clayrune.io/install.ps1 -useb | iex' -ForegroundColor Cyan
        exit 1
    }
}

$claudeVersion = (& claude --version 2>&1 | Select-Object -First 1)
Write-Host "OK Claude CLI: $claudeVersion" -ForegroundColor Green
Write-Host ''

# ── Step 2: Fetch install prompt ───────────────────────────────────────────
Write-Host "Fetching install instructions from $PromptUrl"
try {
    $prompt = (Invoke-WebRequest -Uri $PromptUrl -UseBasicParsing).Content
} catch {
    Write-Host "Failed to fetch install prompt: $_" -ForegroundColor Red
    exit 1
}
Write-Host "OK Got install prompt ($($prompt.Length) bytes)" -ForegroundColor Green
Write-Host ''

# ── Step 3: Disclosure ─────────────────────────────────────────────────────
Write-Host '──────────────────────────────────────' -ForegroundColor Yellow
Write-Host 'About to run:' -ForegroundColor White
Write-Host '  claude --dangerously-skip-permissions -p "<install prompt>"'
Write-Host ''
Write-Host 'Claude will execute commands on your machine to install Clayrune.'
Write-Host 'Estimated time: 3-5 minutes.'
Write-Host "Read the prompt: $PromptUrl"
Write-Host '──────────────────────────────────────' -ForegroundColor Yellow
Write-Host ''

if (-not $env:CLAYRUNE_NO_CONFIRM) {
    Write-Host 'Press Ctrl+C in the next 5 seconds to abort, or wait...'
    Start-Sleep -Seconds 5
}

# ── Step 4: Hand off to Claude ─────────────────────────────────────────────
Write-Host ''
Write-Host '>>> Handing off to Claude' -ForegroundColor White
Write-Host ''

# Pass the prompt via -p so it survives multi-line shell quoting.
& claude --dangerously-skip-permissions -p $prompt

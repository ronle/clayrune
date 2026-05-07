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

# Returns $true iff `claude --version` runs cleanly with non-empty output.
# This is the *real* working-state check — Get-Command alone only proves a
# binary is on PATH, not that it actually runs (the same trap that bit us on
# WSL where npm completed "successfully" but produced a broken CLI).
function Test-ClaudeWorks {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { return $false }
    try {
        $out = & claude --version 2>$null
        return ($LASTEXITCODE -eq 0) -and ($out) -and ($out.ToString().Trim() -ne '')
    } catch {
        return $false
    }
}

Write-Host '======================================' -ForegroundColor Cyan
Write-Host '  Clayrune Installer' -ForegroundColor White
Write-Host '======================================'
Write-Host ''

# ── Step 1: Ensure a working Claude CLI ────────────────────────────────────

if (Test-ClaudeWorks) {
    $claudeVersion = (& claude --version 2>&1 | Select-Object -First 1)
    Write-Host "OK Claude CLI already installed: $claudeVersion" -ForegroundColor Green
    Write-Host ''
} else {
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        Write-Host "Found 'claude' on PATH but it doesn't run cleanly." -ForegroundColor Yellow
        Write-Host 'Will attempt a clean reinstall.'
        Write-Host ''
    } else {
        Write-Host 'Claude CLI not found. Attempting to install...' -ForegroundColor Yellow
        Write-Host ''
    }

    $installed = $false

    # Method 1: npm (preferred on Windows — ships natively with Node).
    if (-not $installed -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host 'Trying npm install -g @anthropic-ai/claude-code...'
        try {
            npm install -g '@anthropic-ai/claude-code'
            Refresh-Path
            if (Test-ClaudeWorks) {
                Write-Host '+ npm install succeeded' -ForegroundColor Green
                Write-Host ''
                $installed = $true
            } else {
                Write-Host "- npm completed but 'claude --version' doesn't work; trying next method..." -ForegroundColor Yellow
                Write-Host ''
            }
        } catch {
            Write-Host "- npm install failed: $_" -ForegroundColor Yellow
            Write-Host ''
        }
    }

    # Method 2: winget Node.js LTS, then npm install.
    if (-not $installed -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'Installing Node.js LTS via winget, then Claude CLI...'
        try {
            winget install --id OpenJS.NodeJS.LTS -e --silent `
                --accept-source-agreements --accept-package-agreements
        } catch {
            Write-Host "- winget install Node.js failed: $_" -ForegroundColor Yellow
            Write-Host ''
        }
        Refresh-Path
        if (Get-Command npm -ErrorAction SilentlyContinue) {
            try {
                npm install -g '@anthropic-ai/claude-code'
                Refresh-Path
                if (Test-ClaudeWorks) {
                    Write-Host '+ winget Node + npm install succeeded' -ForegroundColor Green
                    Write-Host ''
                    $installed = $true
                } else {
                    Write-Host "- npm install completed but claude doesn't run" -ForegroundColor Yellow
                    Write-Host ''
                }
            } catch {
                Write-Host "- npm install (post-winget) failed: $_" -ForegroundColor Yellow
                Write-Host ''
            }
        }
    }

    if (-not $installed) {
        Write-Host ''
        Write-Host 'Could not install a working Claude CLI automatically.' -ForegroundColor Red
        Write-Host ''
        Write-Host 'Manual install options:'
        Write-Host '  Anthropic:  https://docs.anthropic.com/claude-code'
        Write-Host '  npm:        npm install -g @anthropic-ai/claude-code' -ForegroundColor Cyan
        Write-Host ''
        Write-Host 'After installing, verify with:  claude --version'
        Write-Host 'Then re-run this installer in a NEW PowerShell window:'
        Write-Host '  iwr https://clayrune.io/install.ps1 -useb | iex' -ForegroundColor Cyan
        exit 1
    }

    $claudeVersion = (& claude --version 2>&1 | Select-Object -First 1)
    Write-Host "OK Claude CLI: $claudeVersion" -ForegroundColor Green
    Write-Host ''
}

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

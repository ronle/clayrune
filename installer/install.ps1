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

# Returns Node major version on PATH, or 0 if missing/invalid.
function Get-NodeMajor {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { return 0 }
    try {
        $v = (& node --version 2>$null).Trim().TrimStart('v')
        $major = [int]($v -split '\.')[0]
        return $major
    } catch {
        return 0
    }
}

# Ensure Node 18+ is on PATH. Already-good Node → no-op. Old or missing →
# install Node LTS via winget. Must run BEFORE any Claude CLI install attempt
# because npm-installed Claude CLI requires Node 18+ to even parse its own
# source.
function Setup-Node {
    $major = Get-NodeMajor
    if ($major -ge 18) {
        return $true
    }

    if ($major -eq 0) {
        Write-Host 'Node.js not found. Need 18+ for Claude CLI.' -ForegroundColor Yellow
    } else {
        Write-Host "Node.js v$major found - too old for Claude CLI (need 18+)." -ForegroundColor Yellow
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'winget not available; cannot auto-install Node.' -ForegroundColor Red
        Write-Host 'Install Node 20+ manually from https://nodejs.org/ and re-run.'
        return $false
    }

    Write-Host 'Installing Node.js LTS via winget (no admin needed for current user)...'
    try {
        winget install --id OpenJS.NodeJS.LTS -e --silent `
            --accept-source-agreements --accept-package-agreements
    } catch {
        Write-Host "winget install Node.js failed: $_" -ForegroundColor Red
        return $false
    }
    Refresh-Path

    $major = Get-NodeMajor
    if ($major -ge 18) {
        Write-Host "OK Node $((& node --version 2>&1))" -ForegroundColor Green
        Write-Host ''
        return $true
    }
    Write-Host "Node install completed but 'node --version' still reports v$major." -ForegroundColor Red
    Write-Host 'Open a new PowerShell window and re-run.'
    return $false
}

# Returns $true iff bash.exe is reachable OR PowerShell 7+ is the host. Claude
# Code on Windows shells out to bash for its scripting and refuses to run
# without one. Git for Windows ships bash.exe; PowerShell 7+ also satisfies.
function Test-ClaudeRuntimeShell {
    if (Get-Command bash.exe -ErrorAction SilentlyContinue) { return $true }
    if ($PSVersionTable.PSVersion.Major -ge 7) { return $true }
    foreach ($p in @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    )) {
        if (Test-Path $p) {
            # Git is installed but its bin dir isn't on PATH — add it for this
            # session so subsequent `bash` lookups succeed.
            $bin = Split-Path $p
            if (-not (";${env:Path};".ToLower().Contains((";$bin;").ToLower()))) {
                $env:Path = "$bin;$env:Path"
            }
            return $true
        }
    }
    return $false
}

# Ensure Claude Code can run on Windows: install Git for Windows (provides
# bash.exe) if missing. Claude shells out to bash internally; without it the
# CLI errors with "Claude Code on Windows requires either Git for Windows or
# PowerShell" the moment we hand off — this preflight catches that BEFORE we
# spawn the install-prompt subprocess.
function Setup-ClaudeRuntimeShell {
    if (Test-ClaudeRuntimeShell) { return $true }

    Write-Host 'Claude Code needs bash.exe (Git for Windows) or PowerShell 7+ to run.' -ForegroundColor Yellow
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'winget not available — cannot auto-install Git for Windows.' -ForegroundColor Red
        Write-Host 'Install Git for Windows manually from https://git-scm.com/downloads/win and re-run.'
        return $false
    }
    Write-Host 'Installing Git for Windows via winget (also gives Claude its bash runtime)...'
    try {
        winget install --id Git.Git -e --silent --accept-source-agreements --accept-package-agreements
    } catch {
        Write-Host "winget install Git failed: $_" -ForegroundColor Red
        Write-Host 'Install Git for Windows manually from https://git-scm.com/downloads/win and re-run.'
        return $false
    }
    Refresh-Path
    if (Test-ClaudeRuntimeShell) {
        Write-Host 'OK Git for Windows / bash available' -ForegroundColor Green
        Write-Host ''
        return $true
    }
    Write-Host 'Git installed but bash.exe still not on PATH.' -ForegroundColor Red
    Write-Host 'Open a new PowerShell window and re-run this installer.'
    return $false
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

# Returns $true iff Claude CLI is authenticated. Costs a few tokens for users
# who are; for users who aren't, the CLI prints the "Not logged in" sentinel
# without calling the API. We grep for that sentinel rather than rely on exit
# codes (transient errors / rate limits also non-zero).
function Test-ClaudeAuth {
    try {
        $out = (& claude -p "ok" --max-turns 1 2>&1 | Out-String)
    } catch {
        $out = "$_"
    }
    if ($out -match '(?i)not logged in|please run /login') {
        return $false
    }
    return $true
}

Write-Host '======================================' -ForegroundColor Cyan
Write-Host '  Clayrune Installer' -ForegroundColor White
Write-Host '======================================'
Write-Host ''

# ── Step 0: Ensure Node 18+ is available ───────────────────────────────────

if (-not (Setup-Node)) {
    Write-Host ''
    Write-Host 'Could not set up a working Node 18+ runtime automatically.' -ForegroundColor Red
    Write-Host 'Please install Node 20+ from https://nodejs.org/ and re-run.'
    exit 1
}

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

# ── Step 1.4: Verify Claude Code can run (bash.exe / PowerShell 7) ─────────

# Skip on non-Windows (the .ps1 only runs on Windows but be defensive).
if (-not (Setup-ClaudeRuntimeShell)) {
    Write-Host ''
    Write-Host 'Could not provide a runtime shell for Claude Code. Aborting.' -ForegroundColor Red
    exit 1
}

# ── Step 1.5: Verify Claude CLI is authenticated ───────────────────────────

Write-Host 'Checking Claude CLI authentication...'
if (-not (Test-ClaudeAuth)) {
    Write-Host ''
    Write-Host 'Claude CLI is installed but not authenticated.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Easiest path: re-run this installer via the double-click setup' -ForegroundColor White
    Write-Host '(Clayrune-Setup.bat) and pick the [L] option — it logs you in'
    Write-Host 'and continues the install automatically.'
    Write-Host ''
    Write-Host 'Otherwise, do it manually:' -ForegroundColor White
    Write-Host ''
    Write-Host 'Step 1.' -ForegroundColor White -NoNewline; Write-Host ' Open Command Prompt (cmd.exe, NOT PowerShell) and run:'
    Write-Host '         claude /login' -ForegroundColor Cyan
    Write-Host '         (PowerShell users: this fails on default Windows due to ExecutionPolicy.'
    Write-Host '          Use ' -NoNewline; Write-Host 'cmd.exe' -ForegroundColor Cyan -NoNewline; Write-Host ' instead, or run:'
    Write-Host '          ' -NoNewline; Write-Host 'powershell -ExecutionPolicy Bypass -Command "claude /login"' -ForegroundColor Cyan -NoNewline; Write-Host ')'
    Write-Host '         Follow the OAuth prompts (or paste an Anthropic API key).'
    Write-Host '         When you see "' -NoNewline; Write-Host 'Logged in' -ForegroundColor Cyan -NoNewline; Write-Host '", type ' -NoNewline; Write-Host 'exit' -ForegroundColor Cyan -NoNewline; Write-Host ' to leave the Claude REPL.'
    Write-Host ''
    Write-Host 'Step 2.' -ForegroundColor White -NoNewline; Write-Host ' Re-run this installer in a NEW PowerShell window:'
    Write-Host '         $env:CLAYRUNE_PROMPT_URL = ''https://raw.githubusercontent.com/ronle/mission-control/master/installer/install-prompt.md''' -ForegroundColor Cyan
    Write-Host '         iwr https://raw.githubusercontent.com/ronle/mission-control/master/installer/install.ps1 -useb | iex' -ForegroundColor Cyan
    Write-Host ''
    exit 1
}
Write-Host 'OK Authenticated' -ForegroundColor Green
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
Write-Host '>>> Handing off to Claude (this can take 3-5 minutes; progress streams below)' -ForegroundColor White
Write-Host ''

# Stream Claude's output as it works. Without --output-format stream-json,
# `claude -p` only prints the FINAL response — meaning the user sees nothing
# for 3-5 minutes while Claude is running tool calls (Bash, Edit, Write).
# With stream-json + this parser, we surface text blocks (the [STEP N/6]
# markers from the install prompt) and tool-call indicators in real time.
$claudeArgs = @(
    '--dangerously-skip-permissions',
    '-p', $prompt,
    '--print', '--verbose',
    '--output-format', 'stream-json'
)

& claude @claudeArgs |
  ForEach-Object {
    $line = $_
    if (-not $line) { return }
    try {
        $obj = $line | ConvertFrom-Json -ErrorAction Stop
    } catch {
        # Not JSON (banner / stderr leak) — print raw so nothing's hidden.
        Write-Host $line
        return
    }
    if ($obj.type -eq 'assistant' -and $obj.message -and $obj.message.content) {
        foreach ($block in $obj.message.content) {
            if ($block.type -eq 'text' -and $block.text) {
                Write-Host $block.text
            } elseif ($block.type -eq 'tool_use' -and $block.name) {
                Write-Host "  [tool: $($block.name)]" -ForegroundColor DarkGray
            }
        }
    } elseif ($obj.type -eq 'result' -and $obj.is_error) {
        Write-Host "  [error] $($obj.result)" -ForegroundColor Red
    }
    # system / user / result-success events are intentionally suppressed —
    # text blocks already cover everything user-relevant.
  }

# Propagate Claude's exit code so the .bat wrapper shows the right success
# / retry prompt. $LASTEXITCODE is set by the last native command (claude).
exit $LASTEXITCODE

# Install Windows-side prerequisites for Steno with winget.
#
# Steno's dev stack is a bash/make workflow, so run it from WSL. Use scripts/bootstrap.sh inside WSL to
# install the project's Python and Node dependencies. Don't run `uv sync` or `npm install` from Windows
# and then use WSL: the .venv and node_modules folders are platform-specific and won't work across both.
#
# Usage (PowerShell, from the repo root):  powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

$ErrorActionPreference = 'Stop'

function Ok($msg)   { Write-Host "  [ok] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [x]  $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail 'winget not found. Install "App Installer" from the Microsoft Store, then rerun.'
}

function Ensure-Winget($command, $id, $label) {
    if (Get-Command $command -ErrorAction SilentlyContinue) {
        Ok "$label found"
        return
    }
    Warn "$label not found; installing with winget ($id)"
    winget install --id $id --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fail "winget could not install $label. Install it manually and rerun." }
    Ok "$label installed. Open a new terminal so it is on PATH."
}

Write-Host "`nChecking Windows prerequisites"
Ensure-Winget 'uv'   'astral-sh.uv'         'uv'
Ensure-Winget 'node' 'OpenJS.NodeJS.LTS'    'Node.js LTS (needs 20+)'
Ensure-Winget 'java' 'Microsoft.OpenJDK.21' 'Java 21 (for resolver-jvm)'

if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { Ok 'docker running' }
    else { Warn 'Docker is installed but not running. Start Docker Desktop.' }
} else {
    Warn 'Docker Desktop not found. Install it from docker.com and enable WSL integration for your distro.'
}

Write-Host "`nNext: open WSL, cd to this repo, and run: scripts/bootstrap.sh && make dev"

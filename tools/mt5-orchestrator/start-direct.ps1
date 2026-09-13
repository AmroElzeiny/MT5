param(
    [Parameter(Mandatory=$true)][ValidateSet("Claude","Codex")][string]$Actor,
    [int]$Minutes = 720
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "set-direct-mode.ps1") -Actor $Actor -Minutes $Minutes

Push-Location $repo
try {
    if ($Actor -eq "Claude") {
        if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw "Claude CLI not found." }
        & claude
    } else {
        if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { throw "Codex CLI not found." }
        & codex
    }
} finally {
    Pop-Location
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "clear-direct-mode.ps1")
}

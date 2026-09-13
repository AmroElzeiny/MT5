param(
    [Parameter(Mandatory=$true)][ValidateSet("Claude","Codex","Both")][string]$Actor,
    [int]$Minutes = 180
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$current = Join-Path $repo ".mt5-orchestrator\current"
New-Item -ItemType Directory -Force -Path $current | Out-Null

@{
    actor = $Actor
    granted_utc = [DateTime]::UtcNow.ToString("o")
    expires_utc = [DateTime]::UtcNow.AddMinutes($Minutes).ToString("o")
    granted_by = "user-command"
} | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $current "DIRECT_MODE.json")

Write-Host "Direct mode enabled for $Actor for up to $Minutes minutes."
Write-Host "Product Edit/Write/apply_patch is unlocked."
Write-Host "Protected governance, shell mutation, destructive git, and unsafe live-trading actions remain blocked."

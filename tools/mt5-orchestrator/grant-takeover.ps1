param(
    [Parameter(Mandatory=$true)][ValidateSet("Claude","Codex","Both")][string]$Actor,
    [int]$Minutes = 120
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$currentRun = Join-Path $repo ".mt5-orchestrator\current\RUN.json"
if (-not (Test-Path $currentRun)) { throw "No current delegated run." }

$run = Get-Content -Raw $currentRun | ConvertFrom-Json
$runDir = Join-Path $repo ($run.run_dir -replace '/', '\')
$esc = Join-Path $runDir "ESCALATION.json"
if (-not (Test-Path $esc)) { throw "Current run has no ESCALATION.json." }

$j = Get-Content -Raw $esc | ConvertFrom-Json
if ($j.status -ne "ESCALATE_TO_FRONTEND") { throw "Invalid escalation status." }
if ($j.run_id -ne $run.run_id) { throw "Escalation run ID mismatch." }
if (-not $j.needs_frontend_implementation) { throw "Escalation does not authorize front-end implementation." }

$allowed = @($j.allowed_files)
if ($allowed.Count -eq 0) { throw "No files authorized." }

$protected = @("CLAUDE.md","AGENTS.md",".claude\*",".codex\*",".opencode\*",".mt5-orchestrator\*","tools\mt5-orchestrator\*")
foreach ($file in $allowed) {
    $f = ([string]$file -replace '/', '\')
    foreach ($pat in $protected) {
        if ($f -like $pat) { throw "Protected file cannot be unlocked: $file" }
    }
}

@{
    actor = $Actor
    run_id = $run.run_id
    granted_utc = [DateTime]::UtcNow.ToString("o")
    expires_utc = [DateTime]::UtcNow.AddMinutes($Minutes).ToString("o")
    allowed_files = $allowed
} | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $repo ".mt5-orchestrator\current\TAKEOVER.json")

Write-Host "Takeover granted to $Actor for $Minutes minutes."
$allowed | ForEach-Object { Write-Host "  $_" }

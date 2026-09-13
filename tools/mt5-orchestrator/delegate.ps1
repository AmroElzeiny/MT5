param(
    [Parameter(Mandatory=$true)][string]$MissionFile,
    [ValidateSet("Claude","Codex")][string]$FrontEnd,
    [ValidateSet("Standard","Deep")][string]$Tier = "Standard",
    [ValidateSet("Offline","Demo")][string]$RuntimeMode = "Offline"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$mission = (Resolve-Path $MissionFile).Path

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    throw "OpenCode CLI not found."
}

$missionText = Get-Content -Raw $mission

if ($RuntimeMode -eq "Demo" -and $missionText -notmatch '(?m)^\s*Demo\ authorization:\ YES\s*$') {
    throw "Demo mode requires the exact mission line: Demo authorization: YES"
}


$live = Join-Path $repo ".mt5-orchestrator\models\LIVE_MODELS.md"
$refresh = $true
if (Test-Path $live) {
    $age = (Get-Date) - (Get-Item $live).LastWriteTime
    if ($age.TotalHours -lt 24) { $refresh = $false }
}
if ($refresh) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "refresh-models.ps1") -Quiet
}

$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$runDir = Join-Path $repo ".mt5-orchestrator\runs\$runId"
$currentDir = Join-Path $repo ".mt5-orchestrator\current"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
New-Item -ItemType Directory -Force -Path $currentDir | Out-Null

$modeValue = $RuntimeMode
@{
    run_id = $runId
    run_dir = ".mt5-orchestrator/runs/$runId"
    started_utc = [DateTime]::UtcNow.ToString("o")
    front_end = $FrontEnd
    tier = $Tier
    mode = $modeValue
} | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $currentDir "RUN.json")

@{
    run_id = $runId
    status = "starting"
    current_work_package = ""
    active_role = "supervisor"
    active_model = ""
    completed_work_packages = @()
    current_action = "Starting delegated run"
    updated_at_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $runDir "PROGRESS.json")

Copy-Item -LiteralPath $mission -Destination (Join-Path $runDir "MISSION.md")

foreach ($name in @("VISUAL_CONTRACT.md","MIGRATION_CONTRACT.md","ARCHITECT_DECISION.md")) {
    $p = Join-Path $currentDir $name
    if (Test-Path $p) { Copy-Item -LiteralPath $p -Destination (Join-Path $runDir $name) }
}

Push-Location $repo
try {
    git rev-parse HEAD | Set-Content -Encoding UTF8 (Join-Path $runDir "BASELINE_COMMIT.txt")
    git rev-parse --abbrev-ref HEAD | Set-Content -Encoding UTF8 (Join-Path $runDir "BASELINE_BRANCH.txt")
    git status --porcelain=v1 | Set-Content -Encoding UTF8 (Join-Path $runDir "BASELINE_STATUS.txt")
    git diff -- | Set-Content -Encoding UTF8 (Join-Path $runDir "BASELINE_DIFF.patch")
} finally {
    Pop-Location
}

# Best-effort usage snapshot; failure does not abort the mission.
$prev = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    @(& opencode stats --models 20 --project "" 2>&1) |
        ForEach-Object { "$_" } |
        Set-Content -Encoding UTF8 (Join-Path $runDir "USAGE_BEFORE.txt")
} finally {
    $ErrorActionPreference = $prev
}

$agent = if ($Tier -eq "Deep") { "mt5-supervisor-deep" } else { "mt5-supervisor" }
$budgetMinutes = if ($Tier -eq "Deep") { 180 } else { 90 }
$budgetCalls = if ($Tier -eq "Deep") { 12 } else { 8 }

$launch = @"
RUN_ID: $runId
RUN_DIR: .mt5-orchestrator/runs/$runId
FRONT_END: $FrontEnd
TIER: $Tier
RuntimeMode: $modeValue
TARGET_WALL_CLOCK_MINUTES: $budgetMinutes
TARGET_MATERIAL_MODEL_INVOCATIONS: $budgetCalls

Execute the attached mission under the repository and orchestration policies.

The front-end ($FrontEnd) owns architecture and final judgment.
You own delegated execution between event-based checkpoints.

Read:
- CLAUDE.md
- AGENTS.md when present
- .mt5-orchestrator/policy/ROUTING_POLICY.md
- .mt5-orchestrator/policy/BUDGET_POLICY.md
- .mt5-orchestrator/policy/SUPERVISOR_REPORT_CONTRACT.md
- .mt5-orchestrator/models/ROLE_MODEL_MAP.md
- .mt5-orchestrator/models/LIVE_MODELS.md
- .mt5-orchestrator/models/LIVE_MODELS_VERBOSE.txt

Update PROGRESS.json at material role/work-package handoffs.

Use the normal low-cost model set only.
Do not loop past the mission budget to avoid escalation.

Required final evidence:
- .mt5-orchestrator/runs/$runId/SUPERVISOR_REPORT.json
- .mt5-orchestrator/runs/$runId/SUPERVISOR_REPORT.md

If front-end help is required:
- .mt5-orchestrator/runs/$runId/ESCALATION.json

Do not claim completion without evidence.
"@

$launchPath = Join-Path $runDir "LAUNCH_PROMPT.txt"
$launch | Set-Content -Encoding UTF8 $launchPath
$rawTranscript = Join-Path $runDir "SUPERVISOR_STDOUT_RAW.txt"
$cleanTranscript = Join-Path $runDir "SUPERVISOR_STDOUT.txt"

$args = @(
    "run",$launch,
    "--agent",$agent,
    "--auto",
    "--dir",$repo,
    "--title","mt5-$runId",
    "--file",$launchPath,
    "--file",(Join-Path $runDir "MISSION.md")
)
foreach ($name in @("VISUAL_CONTRACT.md","MIGRATION_CONTRACT.md","ARCHITECT_DECISION.md")) {
    $p = Join-Path $runDir $name
    if (Test-Path $p) { $args += @("--file",$p) }
}

Push-Location $repo
$prev = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    # Stream live output to console and raw transcript.
    & opencode @args 2>&1 |
        Tee-Object -FilePath $rawTranscript |
        ForEach-Object { Write-Host "$_" }
    $ocExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prev
    git status --porcelain=v1 | Set-Content -Encoding UTF8 (Join-Path $runDir "FINAL_STATUS.txt")
    git diff -- | Set-Content -Encoding UTF8 (Join-Path $runDir "FINAL_DIFF.patch")
    Pop-Location
}

if (Test-Path $rawTranscript) {
    $rawLines = Get-Content -LiteralPath $rawTranscript
    $cleanLines = @($rawLines | ForEach-Object {
        [regex]::Replace($_, ([char]27).ToString() + '\[[0-?]*[ -/]*[@-~]', '')
    })
    $cleanLines | Set-Content -Encoding UTF8 $cleanTranscript
} else {
    "" | Set-Content -Encoding UTF8 $rawTranscript
    "" | Set-Content -Encoding UTF8 $cleanTranscript
}

$prev = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    @(& opencode stats --models 20 --project "" 2>&1) |
        ForEach-Object { "$_" } |
        Set-Content -Encoding UTF8 (Join-Path $runDir "USAGE_AFTER.txt")
} finally {
    $ErrorActionPreference = $prev
}

if ($ocExit -ne 0) {
    Write-Error "OpenCode exited with code $ocExit. Evidence: $runDir"
    exit $ocExit
}

$report = Join-Path $runDir "SUPERVISOR_REPORT.json"
$reportMd = Join-Path $runDir "SUPERVISOR_REPORT.md"
if (-not (Test-Path $report) -or -not (Test-Path $reportMd)) {
    Write-Error "Supervisor did not produce both required report files. Evidence: $runDir"
    exit 20
}

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = Join-Path $repo "python\.venv\Scripts\python.exe"
}
if (-not (Test-Path $py)) { $py = "python" }

& $py (Join-Path $PSScriptRoot "validate-report.py") $report (Join-Path $repo ".mt5-orchestrator\policy\supervisor-report.schema.json")
if ($LASTEXITCODE -ne 0) {
    Write-Error "Supervisor report failed validation. Evidence: $runDir"
    exit 21
}

$r = Get-Content -Raw $report | ConvertFrom-Json
@"
# Front-end review pointer

Run: $runId
Front-end: $FrontEnd
Report: .mt5-orchestrator/runs/$runId/SUPERVISOR_REPORT.md
JSON: .mt5-orchestrator/runs/$runId/SUPERVISOR_REPORT.json
Diff: .mt5-orchestrator/runs/$runId/FINAL_DIFF.patch
Progress: .mt5-orchestrator/runs/$runId/PROGRESS.json
Verdict: $($r.final_verdict)
"@ | Set-Content -Encoding UTF8 (Join-Path $currentDir "FRONTEND_READ_THIS.md")

if ($r.final_verdict -eq "ESCALATE_TO_FRONTEND") {
    if (-not (Test-Path (Join-Path $runDir "ESCALATION.json"))) {
        Write-Error "Report requests escalation but ESCALATION.json is missing."
        exit 22
    }
    Write-Host "ESCALATE_TO_FRONTEND - see $runDir"
    exit 10
}

Write-Host "Delegated run complete. $FrontEnd should review: $runDir"
exit 0

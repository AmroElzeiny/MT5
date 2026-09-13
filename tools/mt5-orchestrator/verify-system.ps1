param([switch]$RequireBoth)

$ErrorActionPreference = "Continue"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$coreOk = $true
$claudeOk = $true
$codexOk = $true

function Mark([string]$group, [string]$name, [bool]$cond, [string]$detail="") {
    if ($cond) { Write-Host "[PASS][$group] $name $detail" }
    else {
        Write-Host "[FAIL][$group] $name $detail"
        if ($group -eq "CORE") { $script:coreOk = $false }
        elseif ($group -eq "CLAUDE") { $script:claudeOk = $false }
        elseif ($group -eq "CODEX") { $script:codexOk = $false }
    }
}

function Safe-PsSource([string]$Path) {
    try {
        $b = [IO.File]::ReadAllBytes($Path)
        foreach ($x in $b) { if ($x -gt 127) { return $false } }
        return $true
    } catch { return $false }
}

function Parses-Ps([string]$Path) {
    try {
        $t=$null; $e=$null
        [Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e) | Out-Null
        return (@($e).Count -eq 0)
    } catch { return $false }
}

Mark "CORE" "Git repository" (Test-Path (Join-Path $repo ".git"))
Mark "CORE" "OpenCode CLI" ([bool](Get-Command opencode -ErrorAction SilentlyContinue))
Mark "CLAUDE" "Claude CLI" ([bool](Get-Command claude -ErrorAction SilentlyContinue))
Mark "CODEX" "Codex CLI" ([bool](Get-Command codex -ErrorAction SilentlyContinue))

Mark "CORE" "Standard supervisor" (Test-Path (Join-Path $repo ".opencode\agents\mt5-supervisor.md"))
Mark "CORE" "Deep supervisor" (Test-Path (Join-Path $repo ".opencode\agents\mt5-supervisor-deep.md"))
Mark "CORE" "Routing policy" (Test-Path (Join-Path $repo ".mt5-orchestrator\policy\ROUTING_POLICY.md"))
Mark "CORE" "Budget policy" (Test-Path (Join-Path $repo ".mt5-orchestrator\policy\BUDGET_POLICY.md"))
Mark "CORE" "Report schema" (Test-Path (Join-Path $repo ".mt5-orchestrator\policy\supervisor-report.schema.json"))

$psFiles = @()
foreach ($d in @(
    (Join-Path $repo "tools\mt5-orchestrator"),
    (Join-Path $repo ".claude\hooks"),
    (Join-Path $repo ".codex\hooks")
)) {
    if (Test-Path $d) { $psFiles += Get-ChildItem -Path $d -Filter "*.ps1" -File }
}
$badAscii=@(); $badParse=@()
foreach ($f in $psFiles) {
    if (-not (Safe-PsSource $f.FullName)) { $badAscii += $f.Name }
    if (-not (Parses-Ps $f.FullName)) { $badParse += $f.Name }
}
Mark "CORE" "PowerShell ASCII-safe" ($badAscii.Count -eq 0) "($($psFiles.Count) files)"
Mark "CORE" "PowerShell parses" ($badParse.Count -eq 0) "($($psFiles.Count) files)"

if (Get-Command opencode -ErrorAction SilentlyContinue) {
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $go = @(
            & opencode models opencode-go 2>&1 |
            Where-Object { "$_" -match '^opencode-go/' } |
            ForEach-Object { "$_".Trim() }
        )
    } finally { $ErrorActionPreference = $prev }

    Mark "CORE" "OpenCode Go models visible" ($go.Count -gt 0) "($($go.Count) models)"
    foreach ($m in @(
        "opencode-go/minimax-m3",
        "opencode-go/qwen3.8-flash",
        "opencode-go/deepseek-v4.1-flash",
        "opencode-go/deepseek-v4-flash-vision-exp"
    )) {
        Mark "CORE" "Normal model $m" ($go -contains $m)
    }
}

# Ensure no expensive default leaked into this package's agents.
$badModels=@()
$agentDir = Join-Path $repo ".opencode\agents"
if (Test-Path $agentDir) {
    foreach ($f in Get-ChildItem $agentDir -Filter "mt5-*.md" -File) {
        $s = [IO.File]::ReadAllText($f.FullName)
        if ($s -match '(?i)model:\s*opencode-go/(kimi-k2\.7-code|deepseek-v4-pro|qwen3\.8-max|qwen3\.7-max|glm-5\.3)$') {
            $badModels += $f.Name
        }
    }
}
Mark "CORE" "No expensive default agent models" ($badModels.Count -eq 0) ($(if($badModels.Count){"(" + ($badModels -join ", ") + ")"}else{""}))

$claudeEdit = Join-Path $repo ".claude\hooks\mt5-delegation-gate.ps1"
$claudeShell = Join-Path $repo ".claude\hooks\mt5-shell-gate.ps1"
Mark "CLAUDE" "Delegation hook file" (Test-Path $claudeEdit)
Mark "CLAUDE" "Shell hook file" (Test-Path $claudeShell)

$settings = Join-Path $repo ".claude\settings.local.json"
if (Test-Path $settings) {
    try {
        $j = Get-Content -Raw $settings | ConvertFrom-Json
        $t = $j.hooks.PreToolUse | ConvertTo-Json -Depth 12
        Mark "CLAUDE" "Edit/Write hook registered" ($t -match "mt5-delegation-gate")
        Mark "CLAUDE" "Bash hook registered" ($t -match "mt5-shell-gate")
    } catch {
        Mark "CLAUDE" "settings.local.json parses" $false
    }
} else {
    Mark "CLAUDE" "settings.local.json exists" $false
}

$codexEdit = Join-Path $repo ".codex\hooks\mt5-delegation-gate.ps1"
$codexShell = Join-Path $repo ".codex\hooks\mt5-shell-gate.ps1"
Mark "CODEX" "Delegation hook file" (Test-Path $codexEdit)
Mark "CODEX" "Shell hook file" (Test-Path $codexShell)

$hooks = Join-Path $repo ".codex\hooks.json"
if (Test-Path $hooks) {
    try {
        $j = Get-Content -Raw $hooks | ConvertFrom-Json
        $t = $j.hooks.PreToolUse | ConvertTo-Json -Depth 12
        Mark "CODEX" "Edit/apply_patch hook registered" ($t -match "mt5-delegation-gate")
        Mark "CODEX" "Shell hook registered" ($t -match "mt5-shell-gate")
    } catch {
        Mark "CODEX" "hooks.json parses" $false
    }
} else {
    Mark "CODEX" "hooks.json exists" $false
}

Write-Host ""
if ($coreOk) { Write-Host "OPENCode WORKFORCE READY" } else { Write-Host "OPENCode WORKFORCE NOT READY" }
if ($claudeOk) { Write-Host "CLAUDE FRONT-END READY" } else { Write-Host "CLAUDE FRONT-END NOT READY" }
if ($codexOk) { Write-Host "CODEX FRONT-END READY" } else { Write-Host "CODEX FRONT-END NOT READY" }

if ($coreOk -and $claudeOk -and $codexOk) {
    Write-Host "SYSTEM READY FOR BOTH"
    exit 0
}

if ($RequireBoth) {
    Write-Host "SYSTEM NOT READY FOR BOTH"
    exit 1
}

if ($coreOk -and ($claudeOk -or $codexOk)) {
    Write-Host "SYSTEM READY FOR AT LEAST ONE FRONT-END"
    exit 0
}

Write-Host "SYSTEM NOT READY"
exit 1

$ErrorActionPreference = "Stop"
$raw = [Console]::In.ReadToEnd()
try { $evt = $raw | ConvertFrom-Json } catch { exit 0 }

$actor = "Codex"
$cmd = ""
if ($evt.tool_input.command) { $cmd = [string]$evt.tool_input.command }
elseif ($evt.tool_input.cmd) { $cmd = [string]$evt.tool_input.cmd }
elseif ($evt.tool_input.script) { $cmd = [string]$evt.tool_input.script }
if (-not $cmd) { exit 0 }

function Deny([string]$reason) {
    @{
        hookSpecificOutput = @{
            hookEventName = "PreToolUse"
            permissionDecision = "deny"
            permissionDecisionReason = $reason
        }
    } | ConvertTo-Json -Depth 8 -Compress
    exit 0
}

# Never let an agent grant itself Direct mode.
if ($cmd -match '(?i)(set-direct-mode|start-direct)\.ps1') {
    Deny "$actor safety gate: Direct mode must be started by the user from a separate terminal."
}

# Always block destructive remote/version-control actions.
$alwaysBlocked = @(
    '(?i)\bgit\s+push\b',
    '(?i)\bgit\s+reset\s+--hard\b',
    '(?i)\bgit\s+clean\s+-[a-z]*f',
    '(?i)\bgit\s+rebase\b',
    '(?i)\bgit\s+merge\b',
    '(?i)\bgit\s+commit\b'
)
foreach ($pat in $alwaysBlocked) {
    if ($cmd -match $pat) {
        Deny "$actor safety gate: destructive or publishing git actions are blocked inside agent sessions."
    }
}


if ($cmd -match '(?i)(terminal64\.exe|metatester64\.exe).*(/trade|--trade|live-order)') {
    Deny "Trading safety: direct live-trading launch patterns from an agent shell are blocked."
}


# Approved orchestration scripts are allowed.
$approved = @(
    "tools\mt5-orchestrator\delegate.ps1",
    "tools/mt5-orchestrator/delegate.ps1",
    "tools\mt5-orchestrator\refresh-models.ps1",
    "tools/mt5-orchestrator/refresh-models.ps1",
    "tools\mt5-orchestrator\verify-system.ps1",
    "tools/mt5-orchestrator/verify-system.ps1",
    "tools\mt5-orchestrator\watch-run.ps1",
    "tools/mt5-orchestrator/watch-run.ps1",
    "tools\mt5-orchestrator\run-model.ps1",
    "tools/mt5-orchestrator/run-model.ps1",
    "tools\mt5-orchestrator\grant-takeover.ps1",
    "tools/mt5-orchestrator/grant-takeover.ps1",
    "tools\mt5-orchestrator\revoke-takeover.ps1",
    "tools/mt5-orchestrator/revoke-takeover.ps1"
)
foreach ($a in $approved) {
    if ($cmd -like "*$a*") { exit 0 }
}

# Agent shells are read/test/orchestration-only in every mode.
# Direct mode unlocks Edit/Write/apply_patch, not shell file mutation.
$mutationPatterns = @(
    '(?i)\bSet-Content\b',
    '(?i)\bAdd-Content\b',
    '(?i)\bOut-File\b',
    '(?i)\bRemove-Item\b',
    '(?i)\bCopy-Item\b',
    '(?i)\bMove-Item\b',
    '(?i)\bRename-Item\b',
    '(?i)\bNew-Item\b',
    '(?i)(^|[;&|]\s*)rm(\s|$)',
    '(?i)(^|[;&|]\s*)del(\s|$)',
    '(?i)(^|[;&|]\s*)erase(\s|$)',
    '(?i)(^|[;&|]\s*)cp(\s|$)',
    '(?i)(^|[;&|]\s*)mv(\s|$)',
    '(?i)(^|[;&|]\s*)touch(\s|$)',
    '(?i)(^|[;&|]\s*)tee(\s|$)',
    '(?i)\bsed\s+-i\b',
    '(?i)\bperl\s+-pi\b',
    '(?i)\bgit\s+(apply|checkout|restore)\b',
    '(?i)\b(pip|pip3)\s+install\b',
    '(?i)\bpython\s+-m\s+pip\s+install\b',
    '(?i)\b(npm|pnpm|yarn|bun)\s+(install|add|remove|uninstall)\b',
    '(?i)\bfs\.(writeFile|writeFileSync|appendFile|appendFileSync)\b',
    '(?i)\bwrite_text\s*\(',
    '(?i)\bwrite_bytes\s*\('
)
foreach ($pat in $mutationPatterns) {
    if ($cmd -match $pat) {
        Deny "$actor safety gate: shell mutation is blocked. Use Edit/Write/apply_patch in Direct mode, or delegate product edits."
    }
}

$redirectionNormalized = $cmd -replace '\d+>&\d+', ''
if ($redirectionNormalized -match '(?<![<>=])>>?\s*["'']?[^&\s]') {
    Deny "$actor safety gate: shell file redirection is blocked."
}

exit 0

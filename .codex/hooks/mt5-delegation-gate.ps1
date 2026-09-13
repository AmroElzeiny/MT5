$ErrorActionPreference = "Stop"
$raw = [Console]::In.ReadToEnd()
try { $evt = $raw | ConvertFrom-Json } catch { exit 0 }

$actor = "Codex"
$repo = $null
if ($evt.cwd) { $repo = [string]$evt.cwd }
if (-not $repo -and $env:CLAUDE_PROJECT_DIR) { $repo = $env:CLAUDE_PROJECT_DIR }
if (-not $repo) { $repo = (Get-Location).Path }

try { $repo = [System.IO.Path]::GetFullPath($repo) } catch { exit 0 }

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

function Get-ModeMarker {
    $p = Join-Path $repo ".mt5-orchestrator\current\DIRECT_MODE.json"
    if (-not (Test-Path $p)) { return $null }
    try {
        $j = Get-Content -Raw -LiteralPath $p | ConvertFrom-Json
        $expires = [DateTime]::Parse([string]$j.expires_utc).ToUniversalTime()
        if ([DateTime]::UtcNow -gt $expires) { return $null }
        if (($j.actor -eq $actor) -or ($j.actor -eq "Both")) { return $j }
    } catch {}
    return $null
}

function Get-Takeover {
    $p = Join-Path $repo ".mt5-orchestrator\current\TAKEOVER.json"
    if (-not (Test-Path $p)) { return $null }
    try {
        $j = Get-Content -Raw -LiteralPath $p | ConvertFrom-Json
        $expires = [DateTime]::Parse([string]$j.expires_utc).ToUniversalTime()
        if ([DateTime]::UtcNow -gt $expires) { return $null }
        if (($j.actor -eq $actor) -or ($j.actor -eq "Both")) { return $j }
    } catch {}
    return $null
}

function Normalize-Rel([string]$path) {
    if (-not $path) { return $null }
    try {
        if ([System.IO.Path]::IsPathRooted($path)) {
            $full = [System.IO.Path]::GetFullPath($path)
        } else {
            $full = [System.IO.Path]::GetFullPath((Join-Path $repo $path))
        }
        if (-not $full.StartsWith($repo, [System.StringComparison]::OrdinalIgnoreCase)) {
            return "__OUTSIDE__"
        }
        return $full.Substring($repo.Length).TrimStart('\','/')
    } catch {
        return $null
    }
}

function Extract-Paths {
    $paths = @()
    foreach ($k in @("file_path","path","filename")) {
        if ($evt.tool_input.$k) { $paths += [string]$evt.tool_input.$k }
    }
    $patch = $null
    foreach ($k in @("patch","input","diff")) {
        if ($evt.tool_input.$k) { $patch = [string]$evt.tool_input.$k; break }
    }
    if ($patch) {
        foreach ($m in [regex]::Matches($patch, '(?m)^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s+(.+?)\s*$')) {
            $paths += $m.Groups[1].Value.Trim()
        }
        foreach ($m in [regex]::Matches($patch, '(?m)^\+\+\+\s+b/(.+?)\s*$')) {
            $paths += $m.Groups[1].Value.Trim()
        }
    }
    return @($paths | Where-Object { $_ } | Select-Object -Unique)
}

$toolName = [string]$evt.tool_name
if ($toolName -notmatch '^(Edit|Write|apply_patch)$') { exit 0 }

$architectAllowed = @(
    ".mt5-orchestrator\current\MISSION.md",
    ".mt5-orchestrator\current\VISUAL_CONTRACT.md",
    ".mt5-orchestrator\current\MIGRATION_CONTRACT.md",
    ".mt5-orchestrator\current\ARCHITECT_DECISION.md"
)

$protected = @(
    "CLAUDE.md",
    "AGENTS.md",
    ".claude\*",
    ".codex\*",
    ".opencode\*",
    ".mt5-orchestrator\policy\*",
    ".mt5-orchestrator\models\*",
    "tools\*\*"
)

$paths = @(Extract-Paths)
if ($paths.Count -eq 0) {
    if (Get-ModeMarker) { exit 0 }
    Deny "$actor delegation gate: could not prove the target file scope. Product edits must be delegated."
}

$takeover = Get-Takeover
$direct = Get-ModeMarker

foreach ($rawPath in $paths) {
    $rel = Normalize-Rel $rawPath
    if ($rel -eq "__OUTSIDE__") {
        Deny "$actor delegation gate: writing outside the repository is blocked."
    }
    if (-not $rel) {
        if ($direct) { continue }
        Deny "$actor delegation gate: could not normalize target path."
    }

    foreach ($pat in $protected) {
        if ($rel -like $pat) {
            Deny "$actor delegation gate: governance/orchestrator file '$rel' is protected."
        }
    }

    $isArchitect = $false
    foreach ($a in $architectAllowed) {
        if ($rel -ieq $a) { $isArchitect = $true; break }
    }
    if ($isArchitect) { continue }

    if ($direct) { continue }

    if ($takeover) {
        $allowed = @($takeover.allowed_files)
        $ok = $false
        foreach ($pat in $allowed) {
            $n = ([string]$pat -replace '/', '\')
            if ($rel -like $n) { $ok = $true; break }
        }
        if ($ok) { continue }
    }

    Deny "$actor delegation gate: product edits belong to OpenCode Go. Use delegated mode, a valid escalation takeover, or a user-started Direct session."
}

exit 0

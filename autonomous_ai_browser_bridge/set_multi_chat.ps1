param(
    [ValidateRange(1, 20)][int]$Tabs = 3,
    [ValidateRange(1, 1000)][int]$RequestsPerTab = 3
)

$ErrorActionPreference = "Stop"
$envPath = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) { throw "Bridge .env not found: $envPath" }

$lines = [System.Collections.Generic.List[string]]::new()
Get-Content -LiteralPath $envPath | ForEach-Object { [void]$lines.Add($_) }

function Set-EnvValue([string]$Name, [string]$Value) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match ('^' + [regex]::Escape($Name) + '=')) {
            $lines[$i] = "$Name=$Value"
            return
        }
    }
    [void]$lines.Add("$Name=$Value")
}

Set-EnvValue "BROWSER_CHAT_TAB_COUNT" ([string]$Tabs)
Set-EnvValue "BROWSER_REQUESTS_PER_TAB" ([string]$RequestsPerTab)
Set-EnvValue "BROWSER_START_URL" "https://chatgpt.com/?temporary-chat=true"
Set-EnvValue "BROWSER_CHAT_NEW_URL" "https://chatgpt.com/?temporary-chat=true"
Set-EnvValue "BROWSER_CHAT_TAB_URLS" ""

[System.IO.File]::WriteAllLines($envPath, $lines, [System.Text.UTF8Encoding]::new($false))
Write-Host "Configured $Tabs independent temporary-chat lanes, refreshed after $RequestsPerTab submitted roles."
Write-Host "Restart run_bridge.ps1 for the change to take effect."

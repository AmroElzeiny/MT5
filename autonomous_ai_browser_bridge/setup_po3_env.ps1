param(
  [Parameter(Mandatory=$true)][string]$Po3EnvPath,
  [string]$ModelId = "chatgpt-browser-review"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (!(Test-Path .env)) {
  & .\.venv\Scripts\python.exe -c "from bridge.config import settings; print(settings.base_url)" | Out-Null
}
$keyLine = Get-Content .env | Where-Object { $_ -match '^BRIDGE_API_KEY=' } | Select-Object -First 1
if (!$keyLine) { throw "BRIDGE_API_KEY not found in bridge .env" }
$key = $keyLine.Substring('BRIDGE_API_KEY='.Length)
$managed = [ordered]@{
  'AI_USE_REMOTE_API'='false'
  'LOCAL_AI_BASE_URL'='http://127.0.0.1:1234/v1'
  'LOCAL_AI_API_KEY'=$key
  'LOCAL_AI_MODEL'=$ModelId
  'LOCAL_AI_ANALYST_MODEL'=$ModelId
  'LOCAL_AI_CRITIC_MODEL'=$ModelId
  'LOCAL_AI_ADJUDICATOR_MODEL'=$ModelId
  'LOCAL_AI_FALLBACK_MODELS'=''
  'LOCAL_AI_HEALTHCHECK_PATH'='/models'
  'LOCAL_AI_TIMEOUT_SEC'='1800'
  'LOCAL_AI_ANALYST_TIMEOUT_SEC'='900'
  'LOCAL_AI_CRITIC_TIMEOUT_SEC'='450'
  'LOCAL_AI_ADJUDICATOR_TIMEOUT_SEC'='300'
  'AI_PROVIDER_ANALYST_TIMEOUT_SEC'='900'
  'AI_PROVIDER_CRITIC_TIMEOUT_SEC'='450'
  'AI_PROVIDER_ADJUDICATOR_TIMEOUT_SEC'='300'
  'LOCAL_AI_MAX_RETRIES'='0'
  'LOCAL_AI_MAX_OUTPUT_TOKENS'='32768'
  'LOCAL_AI_TEMPERATURE'='0.15'
  'LOCAL_AI_TOP_P'='0.85'
  'LOCAL_AI_SEED'='42'
  'LOCAL_AI_ENABLE_THINKING'='false'
  'LOCAL_AI_REQUIRE_JSON_SCHEMA'='true'
  'LOCAL_AI_PARALLELISM'='3'
  'LOCAL_AI_CONTEXT_BUDGET_TOKENS'='131072'
  'AI_MT5_TERMINAL_TIMEOUT_SEC'='1800'
  'AI_RESPONSE_WRITE_MARGIN_SEC'='15'
  'AI_MIN_PROVIDER_ATTEMPT_SEC'='10'
  'AI_LIVE_CANDIDATE_BUDGET'='3'
  'AI_SHADOW_COMPARE_PROVIDERS'='false'
  'AI_ENABLE_SNAPSHOTS'='false'
}
$existing = @()
if (Test-Path $Po3EnvPath) { $existing = Get-Content $Po3EnvPath }
$filtered = foreach ($line in $existing) {
  if ($line -match '^([A-Z0-9_]+)=') { if ($managed.Contains($matches[1])) { continue } }
  $line
}
$out = @($filtered) + "" + @($managed.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
Set-Content -Path $Po3EnvPath -Value $out -Encoding UTF8
Write-Host "PO3 env updated for autonomous browser bridge: $Po3EnvPath"
Write-Host "Configured local model ID: $ModelId"

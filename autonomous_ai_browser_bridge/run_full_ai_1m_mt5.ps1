$ErrorActionPreference = "Stop"

$terminal = "C:\Program Files\FxPro - MetaTrader 5\terminal64.exe"
$config = "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\logs\full_ai_1m_real_ai_backtest.ini"

if (-not (Test-Path -LiteralPath $terminal)) { throw "FxPro terminal not found: $terminal" }
if (-not (Test-Path -LiteralPath $config)) { throw "Tester config not found: $config" }

& $terminal "/config:$config"

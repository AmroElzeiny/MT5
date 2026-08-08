Set-Location $PSScriptRoot
& .\.venv\Scripts\Activate.ps1
Start-Process "http://127.0.0.1:1234/"
python -m bridge

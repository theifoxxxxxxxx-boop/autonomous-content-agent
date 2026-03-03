param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"

Write-Host "Starting backend on port $BackendPort ..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$backendDir'; .\.venv\Scripts\Activate.ps1; uvicorn src.main:app --reload --port $BackendPort"
)

Write-Host "Starting frontend on port $FrontendPort ..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$frontendDir'; npm run dev -- -p $FrontendPort"
)

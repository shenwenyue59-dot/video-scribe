$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$ngrok = Join-Path $root '.runtime\ngrok\ngrok.exe'
$domain = 'https://swoop-manila-manicure.ngrok-free.dev'

$appRunning = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $appRunning) {
    Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '8765') -WorkingDirectory $root -WindowStyle Hidden
}

$tunnelRunning = $false
try {
    $tunnels = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 2
    $tunnelRunning = [bool]($tunnels.tunnels | Where-Object public_url -eq $domain)
} catch {}

if ($tunnelRunning) {
    try {
        $health = Invoke-RestMethod -Uri "$domain/api/health" -Headers @{'ngrok-skip-browser-warning' = '1'} -TimeoutSec 8
        $tunnelRunning = $health.status -eq 'ok'
    } catch {
        $tunnelRunning = $false
    }
}

if (-not $tunnelRunning) {
    Get-Process -Name ngrok -ErrorAction SilentlyContinue |
        Where-Object {$_.Path -eq $ngrok} |
        Stop-Process -Force
    $env:HTTP_PROXY = ''
    $env:HTTPS_PROXY = ''
    $env:ALL_PROXY = ''
    Start-Process -FilePath $ngrok -ArgumentList @('http', '8765', '--url', $domain) -WorkingDirectory $root -WindowStyle Hidden
}

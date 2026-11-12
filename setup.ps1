$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.runtime\python'
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.runtime\cache'
$env:PYTHONUTF8 = '1'
$uv = Join-Path $PSScriptRoot '.runtime\uv.exe'
if (!(Test-Path -LiteralPath $uv)) {
    New-Item -ItemType Directory -Force -Path '.runtime' | Out-Null
    Write-Host 'Downloading the local Python installer...'
    $archive = Join-Path $PSScriptRoot '.runtime\uv.zip'
    Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath '.runtime' -Force
    Remove-Item -LiteralPath $archive
}
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $uv venv --python 3.12 --managed-python .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python setup failed. Check your network and retry.' }
}
& $uv pip install --python '.venv\Scripts\python.exe' -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
$appPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $appPython -c 'import onnxruntime' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing the Microsoft C++ runtime required by local subtitle OCR...'
    $vcRedist = Join-Path $PSScriptRoot '.runtime\vc_redist.x64.exe'
    Invoke-WebRequest 'https://aka.ms/vc14/vc_redist.x64.exe' -OutFile $vcRedist
    $installer = Start-Process -FilePath $vcRedist -ArgumentList @('/install', '/quiet', '/norestart') -Wait -PassThru
    if ($installer.ExitCode -notin 0, 1638, 3010) {
        throw "Microsoft C++ runtime installation failed with code $($installer.ExitCode)."
    }
    & $appPython -c 'import onnxruntime'
    if ($LASTEXITCODE -ne 0) { throw 'Local subtitle OCR runtime is still unavailable. Restart Windows and run setup.ps1 again.' }
}
Set-Content -LiteralPath '.runtime\ready' -Value 'ok'
Write-Host 'Setup complete.'

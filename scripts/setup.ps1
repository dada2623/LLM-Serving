$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsDirectory = Join-Path $projectRoot '.tools'
$uvExecutable = Join-Path $toolsDirectory 'uv.exe'
$uvVersion = '0.13.6'
$pythonVersion = (Get-Content -LiteralPath (Join-Path $projectRoot '.python-version') -Raw).Trim()

if (-not (Test-Path -LiteralPath $uvExecutable)) {
    New-Item -ItemType Directory -Path $toolsDirectory -Force | Out-Null
    $archivePath = Join-Path $toolsDirectory 'uv.zip'
    Invoke-WebRequest -Uri "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $archivePath
    Expand-Archive -LiteralPath $archivePath -DestinationPath $toolsDirectory -Force
}

$previousPythonDirectory = $env:UV_PYTHON_INSTALL_DIR
$previousCacheDirectory = $env:UV_CACHE_DIR
$previousSystemCertificates = $env:UV_SYSTEM_CERTS
try {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.python'
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.cache/uv'
    $env:UV_SYSTEM_CERTS = 'true'
    Push-Location $projectRoot
    try {
        & $uvExecutable python install $pythonVersion --no-bin --no-registry
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
        if (Test-Path -LiteralPath (Join-Path $projectRoot 'uv.lock')) {
            & $uvExecutable sync --locked
        } else {
            & $uvExecutable sync
        }
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & (Join-Path $projectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'verify_cuda.py')
        if ($LASTEXITCODE -ne 0) { throw 'CUDA verification failed.' }
    } finally {
        Pop-Location
    }
} finally {
    $env:UV_PYTHON_INSTALL_DIR = $previousPythonDirectory
    $env:UV_CACHE_DIR = $previousCacheDirectory
    $env:UV_SYSTEM_CERTS = $previousSystemCertificates
}

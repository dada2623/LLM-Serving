$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$installerDirectory = Join-Path $projectRoot '.tools/installers'
New-Item -ItemType Directory -Path $installerDirectory -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'outputs') -Force | Out-Null
New-Item -ItemType Directory -Path 'D:\Docker\wsl' -Force | Out-Null

function Get-SignedInstaller {
    param([string]$Name, [string]$Url, [string]$Publisher)
    $destination = Join-Path $installerDirectory $Name
    if (-not (Test-Path -LiteralPath $destination)) {
        & curl.exe --fail --location --retry 3 --output $destination $Url
        if ($LASTEXITCODE -ne 0) { throw "Unable to download $Name." }
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $destination
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $Publisher) {
        throw "Installer signature validation failed: $Name"
    }
    return $destination
}

$dockerInstaller = Get-SignedInstaller -Name 'DockerDesktopInstaller.exe' `
    -Url 'https://desktop.docker.com/win/main/amd64/244584/Docker%20Desktop%20Installer.exe' -Publisher 'Docker Inc'
$null = Get-SignedInstaller -Name 'wsl.x64.msi' `
    -Url 'https://github.com/microsoft/WSL/releases/download/3.0.3/wsl.3.0.3.0.x64.msi' -Publisher 'Microsoft Corporation'

$features = Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform' OR Name='Microsoft-Windows-Subsystem-Linux'"
$needsPreparation = @($features | Where-Object { $_.InstallState -ne 1 }).Count -gt 0
$wslService = Get-Service WslService -ErrorAction SilentlyContinue
if ($needsPreparation -or -not $wslService) {
    Write-Output 'Preparing WSL 2. Windows may display an administrator/UAC prompt. No automatic restart.'
    $adminArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        ('"' + (Join-Path $PSScriptRoot 'enable-wsl.ps1') + '"'), '-ProjectRoot', ('"' + $projectRoot + '"'))
    $adminProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList $adminArguments `
        -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    $reportPath = Join-Path $projectRoot 'outputs/wsl-preparation.json'
    if ($adminProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) {
        if (Test-Path -LiteralPath $reportPath) { Get-Content -LiteralPath $reportPath | Write-Output }
        throw 'WSL preparation did not finish. Run this script with administrator approval.'
    }
    $preparation = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if (-not $preparation.success) { throw $preparation.error }
    if ($preparation.restart_required) {
        Write-Output 'Windows restart is required for WSL GPU containers. No restart will be triggered.'
    }
}

$desktopExecutable = Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/Docker Desktop.exe'
if (-not (Test-Path -LiteralPath $desktopExecutable)) {
    $installation = Start-Process -FilePath $dockerInstaller -ArgumentList @(
        'install', '--user', '--quiet', '--accept-license', '--backend=wsl-2',
        '--wsl-default-data-root=D:\Docker\wsl', '--no-windows-containers'
    ) -WindowStyle Hidden -Wait -PassThru
    if ($installation.ExitCode -notin @(0, 3010)) {
        throw "Docker Desktop installation failed (exit $($installation.ExitCode)). Check $env:LOCALAPPDATA\Docker\install-log.txt. If WSL was just enabled, reboot Windows and run this script again."
    }
}
if ((Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
    Start-Process -FilePath $desktopExecutable -WindowStyle Hidden
    Write-Output 'Docker Desktop started. Wait for its engine, then run start.cmd.'
} else {
    Write-Output 'Docker Desktop installed. Reboot Windows, start Docker Desktop, then run start.cmd.'
}

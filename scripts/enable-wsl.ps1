#Requires -RunAsAdministrator
param([Parameter(Mandatory = $true)][string]$ProjectRoot)

$ErrorActionPreference = 'Stop'
$reportPath = Join-Path $ProjectRoot 'outputs/wsl-preparation.json'
$installerPath = Join-Path $ProjectRoot '.tools/installers/wsl.x64.msi'
$report = @{ success = $false; restart_required = $false; error = $null }
try {
    $features = Enable-WindowsOptionalFeature -Online -FeatureName @(
        'Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform'
    ) -All -NoRestart
    $report.restart_required = [bool]($features | Where-Object { $_.RestartNeeded })
    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
        throw 'WSL installer signature validation failed.'
    }
    $logPath = Join-Path $ProjectRoot 'outputs/wsl-install.log'
    $installation = Start-Process -FilePath 'msiexec.exe' -ArgumentList @(
        '/i', ('"' + $installerPath + '"'), '/qn', '/norestart', '/L*v', ('"' + $logPath + '"')
    ) -WindowStyle Hidden -Wait -PassThru
    $report.msi_exit_code = $installation.ExitCode
    if ($installation.ExitCode -notin @(0, 3010, 1638)) {
        throw "WSL installation failed (exit $($installation.ExitCode)). Check outputs/wsl-install.log."
    }
    if ($installation.ExitCode -eq 3010) { $report.restart_required = $true }
    & bcdedit.exe /set hypervisorlaunchtype auto
    if ($LASTEXITCODE -ne 0) { throw 'Unable to enable the Windows hypervisor at boot.' }
    if (-not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
        $report.restart_required = $true
    }
    $report.success = $true
} catch {
    $report.error = $_.Exception.Message
} finally {
    $report | ConvertTo-Json | Set-Content -LiteralPath $reportPath -Encoding utf8
}
if (-not $report.success) { exit 1 }

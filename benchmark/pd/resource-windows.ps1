param([Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
while (-not (Test-Path -LiteralPath ($OutputPath + '.STOP'))) {
    $row = @{ time = [DateTimeOffset]::UtcNow.ToString('o') }
    try {
        $osInfo = Get-CimInstance Win32_OperatingSystem
        $row.memory = @{ totalKiB = $osInfo.TotalVisibleMemorySize; freeKiB = $osInfo.FreePhysicalMemory }
        $row.gpuMemory = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory |
            Select-Object Name,DedicatedUsage,SharedUsage,TotalCommitted)
        $row.processes = @(Get-Process | Where-Object { $_.ProcessName -match 'ssh|wsl|vmmem|docker|python' } |
            Select-Object Id,ProcessName,CPU,WorkingSet64,PrivateMemorySize64)
    } catch { $row.error = $_.Exception.Message }
    $row | ConvertTo-Json -Depth 5 -Compress | Add-Content -LiteralPath $OutputPath -Encoding UTF8
    Start-Sleep -Seconds 10
}

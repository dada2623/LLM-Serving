param(
    [string]$RunDirectory = 'benchmark\baseline\local',
    [switch]$Foreground
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runRoot = if ([System.IO.Path]::IsPathRooted($RunDirectory)) { $RunDirectory } else { Join-Path $projectRoot $RunDirectory }
if (-not (Test-Path -LiteralPath (Join-Path $runRoot 'config.json'))) { throw 'Prepare the frozen baseline batch first.' }
if (Test-Path -LiteralPath (Join-Path $runRoot 'RUNNING.lock')) { throw 'Inspect the current PID and execution state before resuming; a baseline lock exists.' }
& docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not ready.' }
$pythonRoot = Join-Path $projectRoot '.python\cpython-3.12.16-windows-x86_64-none'
$baselinePython = Join-Path $env:TEMP 'llm-p2-python\python.exe'
if (-not (Test-Path -LiteralPath $baselinePython)) { throw 'The portable Docker-capable Python is missing; do not overwrite an active runtime.' }
$env:PYTHONHOME = $pythonRoot
$script = Join-Path $PSScriptRoot 'run_baseline.py'
if ($Foreground) {
    & $baselinePython $script run --root $runRoot
    exit $LASTEXITCODE
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $runRoot "runner-$stamp.stdout.log"
$stderr = Join-Path $runRoot "runner-$stamp.stderr.log"
$arguments = @('"' + $script + '"', 'run', '--root', '"' + $runRoot + '"')
$process = Start-Process -FilePath $baselinePython -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
[pscustomobject]@{PID=$process.Id; RunDirectory=$runRoot; Stdout=$stdout; Stderr=$stderr} | ConvertTo-Json

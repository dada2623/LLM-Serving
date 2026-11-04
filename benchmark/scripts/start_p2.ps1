param(
    [string]$RunDirectory = 'benchmark\p2\local',
    [switch]$Foreground
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runRoot = if ([System.IO.Path]::IsPathRooted($RunDirectory)) { $RunDirectory } else { Join-Path $projectRoot $RunDirectory }
if (-not (Test-Path -LiteralPath (Join-Path $runRoot 'config.json'))) { throw 'Prepare the P2 dataset and config first.' }
if (Test-Path -LiteralPath (Join-Path $runRoot 'RUNNING.lock')) { throw 'A P2 lock already exists; inspect execution.json and its PID before resuming.' }
& docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not ready.' }
$pythonRoot = Join-Path $projectRoot '.python\cpython-3.12.16-windows-x86_64-none'
$p2Python = Join-Path $env:TEMP 'llm-p2-python'
New-Item -ItemType Directory -Path $p2Python -Force | Out-Null
foreach ($name in @('python.exe','python312.dll')) {
    Copy-Item -LiteralPath (Join-Path $pythonRoot $name) -Destination (Join-Path $p2Python $name) -Force
}
$env:PYTHONHOME = $pythonRoot
$exe = Join-Path $p2Python 'python.exe'
$script = Join-Path $PSScriptRoot 'run_p2.py'
if ($Foreground) {
    & $exe $script run --root $runRoot
    exit $LASTEXITCODE
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $runRoot "runner-$stamp.stdout.log"
$stderr = Join-Path $runRoot "runner-$stamp.stderr.log"
$arguments = @('"' + $script + '"', 'run', '--root', '"' + $runRoot + '"')
$process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
[pscustomobject]@{PID=$process.Id; RunDirectory=$runRoot; Stdout=$stdout; Stderr=$stderr} | ConvertTo-Json

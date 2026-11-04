param(
    [ValidateSet('start', 'stop', 'status', 'logs', 'gpu-check', 'verify')]
    [string]$Action = 'start',
    [ValidateSet('all', 'vllm', 'sglang')]
    [string]$Service = $(if ($Action -eq 'start') { 'vllm' } else { 'all' })
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) {
    $dockerExecutable = $dockerCommand.Source
} else {
    $dockerExecutable = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $dockerExecutable) {
    throw 'Docker Desktop is missing. Run scripts/install-container-prereqs.ps1 first.'
}
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination $envFile
}
$composeArguments = @('compose', '--project-directory', $projectRoot,
    '--env-file', $envFile, '-f', (Join-Path $projectRoot 'compose.yaml'))
$services = if ($Service -eq 'all') { @('vllm', 'sglang') } else { @($Service) }
if ($Action -eq 'start' -and $Service -eq 'all') {
    throw 'This single-GPU model profile runs one server at a time. Choose -Service vllm or -Service sglang.'
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    & $dockerExecutable @composeArguments @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed (exit $LASTEXITCODE)." }
}

function Get-DockerEngineState {
    # Windows PowerShell 5 treats redirected native stderr as error records.
    $ErrorActionPreference = 'Continue'
    $information = & $dockerExecutable info --format '{{json .}}' 2>$null
    if ($LASTEXITCODE -ne 0) { return @{ Available = $false } }
    $parsed = ($information -join "`n") | ConvertFrom-Json
    return @{ Available = $true; Runtimes = $parsed.Runtimes; OSType = $parsed.OSType }
}

function Test-Services {
    $configurationText = & $dockerExecutable @composeArguments config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read Compose configuration.' }
    $configuration = ($configurationText -join "`n") | ConvertFrom-Json
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Local Python environment is missing.' }
    foreach ($name in $services) {
        $gpuReport = Join-Path $projectRoot "outputs/$name-gpu-check.json"
        & $dockerExecutable @composeArguments exec -T $name python3 /opt/llm/gpu_probe.py |
            Tee-Object -FilePath $gpuReport
        if ($LASTEXITCODE -ne 0) { throw "$name container GPU verification failed." }
        $serviceConfiguration = $configuration.services.$name
        $port = $serviceConfiguration.ports[0].published
        $previousKey = $env:LLM_API_KEY
        try {
            $env:LLM_API_KEY = $serviceConfiguration.environment.LLM_API_KEY
            & $python (Join-Path $projectRoot 'docker/api_probe.py') --base-url "http://127.0.0.1:$port/v1" |
                Tee-Object -FilePath (Join-Path $projectRoot "outputs/$name-api-check.json")
            if ($LASTEXITCODE -ne 0) { throw "$name API verification failed." }
        } finally { $env:LLM_API_KEY = $previousKey }
    }
}

Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'outputs') -Force | Out-Null
    Invoke-Compose -ComposeArgs @('config', '--quiet')
    $engineState = Get-DockerEngineState
    if (-not $engineState.Available -and $Action -in @('start', 'gpu-check')) {
        if (-not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
            throw 'Windows must be restarted to activate WSL 2 / Virtual Machine Platform. After restarting, run start.cmd again.'
        }
        $desktopExecutable = Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/Docker Desktop.exe'
        if (-not (Test-Path -LiteralPath $desktopExecutable)) {
            $desktopExecutable = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
        }
        if (Test-Path -LiteralPath $desktopExecutable) {
            Write-Output 'Starting Docker Desktop; waiting up to 180 seconds for the engine...'
            Start-Process -FilePath $desktopExecutable -WindowStyle Hidden
            $deadline = (Get-Date).AddSeconds(180)
            while (-not $engineState.Available -and (Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 3
                $engineState = Get-DockerEngineState
            }
        }
    }
    if (-not $engineState.Available) {
        throw 'Docker engine is unavailable. Start Docker Desktop and check its WSL 2 backend.'
    }
    if ($engineState.OSType -ne 'linux') { throw 'Switch Docker Desktop to Linux containers / WSL 2.' }
    $runtimeInformation = $engineState.Runtimes | ConvertTo-Json -Depth 5 -Compress
    $runtimeInformation | Set-Content -LiteralPath (Join-Path $projectRoot 'outputs/docker-runtimes.json') -Encoding utf8
    Write-Output ('Docker host runtimes: ' + (($engineState.Runtimes.PSObject.Properties | ForEach-Object { $_.Name + '=' + $_.Value.path }) -join ', '))
    if ($Action -in @('start', 'gpu-check')) {
        $configurationText = & $dockerExecutable @composeArguments config --format json
        if ($LASTEXITCODE -ne 0) { throw 'Unable to read Compose mounts.' }
        $configuration = ($configurationText -join "`n") | ConvertFrom-Json
        foreach ($name in $services) {
            foreach ($volume in $configuration.services.$name.volumes) {
                if ($volume.type -eq 'bind') {
                    New-Item -ItemType Directory -Path $volume.source -Force | Out-Null
                }
            }
        }
    }
    switch ($Action) {
        'start' {
            $otherService = if ($Service -eq 'vllm') { 'sglang' } else { 'vllm' }
            Invoke-Compose -ComposeArgs @('stop', $otherService)
            Invoke-Compose -ComposeArgs (@('up', '-d', '--build', '--wait', '--wait-timeout', '1800') + $services)
            Test-Services
            Invoke-Compose -ComposeArgs (@('ps') + $services)
        }
        'stop' {
            if ($Service -eq 'all') { Invoke-Compose -ComposeArgs @('down') }
            else { Invoke-Compose -ComposeArgs @('stop', $Service) }
        }
        'status' { Invoke-Compose -ComposeArgs (@('ps', '--all') + $services) }
        'logs' { Invoke-Compose -ComposeArgs (@('logs', '--follow', '--tail', '100') + $services) }
        'gpu-check' {
            Invoke-Compose -ComposeArgs (@('build') + $services)
            foreach ($name in $services) {
                & $dockerExecutable @composeArguments run --rm --no-deps -T `
                    --entrypoint python3 $name /opt/llm/gpu_probe.py |
                    Tee-Object -FilePath (Join-Path $projectRoot "outputs/$name-gpu-check.json")
                if ($LASTEXITCODE -ne 0) { throw "$name container GPU verification failed." }
            }
        }
        'verify' { Test-Services }
    }
} finally { Pop-Location }

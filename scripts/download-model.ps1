param(
    [string]$ModelId = 'Qwen/Qwen3-0.6B',
    [string]$Revision = 'master',
    [string]$Destination = './models\Qwen3-0.6B'
)

$ErrorActionPreference = 'Stop'

function Get-Sha256Hex([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

$destinationRoot = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
$manifestPath = Join-Path $destinationRoot '.modelscope-manifest.json'
$revisionEncoded = [Uri]::EscapeDataString($Revision)
$manifestUrl = "https://modelscope.cn/api/v1/models/$ModelId/repo/files?Revision=$revisionEncoded&Recursive=true"
& curl.exe --fail --silent --show-error --location --retry 3 --connect-timeout 30 --max-time 120 $manifestUrl -o $manifestPath
if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch ModelScope file metadata.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if (-not $manifest.Success -or -not $manifest.Data.Files) { throw 'Unexpected ModelScope metadata response.' }
$files = @($manifest.Data.Files | Where-Object { $_.Type -ne 'tree' })
foreach ($file in $files) {
    if (-not $file.Sha256) { throw "Missing SHA-256 metadata for $($file.Path)." }
    $target = [IO.Path]::GetFullPath((Join-Path $destinationRoot $file.Path))
    if (-not $target.StartsWith($destinationRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Repository file path is outside the destination directory.'
    }
    if (Test-Path -LiteralPath $target) {
        if ((Get-Sha256Hex $target) -eq $file.Sha256) {
            Write-Output "Already verified: $($file.Path)"
            continue
        }
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    $partial = $target + '.download'
    if ((Test-Path -LiteralPath $partial) -and (Get-Item -LiteralPath $partial).Length -eq $file.Size) {
        if ((Get-Sha256Hex $partial) -eq $file.Sha256) {
            Move-Item -LiteralPath $partial -Destination $target -Force
            Write-Output "Verified completed download: $($file.Path)"
            continue
        }
        throw "SHA-256 mismatch: $partial"
    }
    $relativeUrl = ($file.Path -split '/' | ForEach-Object { [Uri]::EscapeDataString($_) }) -join '/'
    $url = "https://modelscope.cn/api/v1/models/$ModelId/repo?Revision=$revisionEncoded&FilePath=$relativeUrl"
    Write-Output ("Downloading {0} ({1:N1} MiB)" -f $file.Path, ($file.Size / 1MB))
    $downloadComplete = $false
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        & curl.exe --fail --silent --show-error --location --retry 3 --connect-timeout 30 --max-time 3600 --speed-limit 1024 --speed-time 120 --continue-at - $url -o $partial
        $downloadExitCode = $LASTEXITCODE
        if ((Test-Path -LiteralPath $partial) -and (Get-Item -LiteralPath $partial).Length -eq $file.Size) {
            $downloadComplete = $true
            break
        }
        if ($attempt -lt 12) {
            Write-Output "Transfer incomplete (curl exit $downloadExitCode); resuming $($file.Path), attempt $($attempt + 1)/12."
            Start-Sleep -Seconds 5
        }
    }
    if (-not $downloadComplete) { throw "Download failed after 12 attempts: $($file.Path). Re-run to resume." }
    if ((Get-Sha256Hex $partial) -ne $file.Sha256) {
        throw "SHA-256 mismatch: $partial"
    }
    Move-Item -LiteralPath $partial -Destination $target -Force
    Write-Output "Verified: $($file.Path)"
}
Write-Output "Model ready: $destinationRoot"

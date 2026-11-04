#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory = $true)]
    [string]$Gateway,
    [Parameter(Mandatory = $true)]
    [int]$InterfaceIndex,
    [string[]]$HostNames = @(
        'huggingface.co', 'cdn-lfs.huggingface.co', 'cdn-lfs-us-1.hf.co',
        'cdn-lfs-eu-1.hf.co', 'cas-bridge.xethub.hf.co',
        'cas-server.xethub.hf.co', 'transfer.xethub.hf.co',
        'us.aws.cdn.hf.co', 'eu.aws.cdn.hf.co', 'aws.cdn.hf.co'
    )
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$entries = @()
$failures = @()
foreach ($hostname in $HostNames) {
    try {
        $addresses = @(Resolve-DnsName -Name $hostname -Type A -ErrorAction Stop |
            Where-Object { $_.IPAddress -and $_.IPAddress -notmatch ':' } |
            Select-Object -ExpandProperty IPAddress -Unique)
        foreach ($address in $addresses) {
            $prefix = "$address/32"
            $existing = Get-NetRoute -DestinationPrefix $prefix -PolicyStore ActiveStore -ErrorAction SilentlyContinue |
                Where-Object { $_.InterfaceIndex -eq $InterfaceIndex -and $_.NextHop -eq $Gateway }
            if (-not $existing) {
                New-NetRoute -DestinationPrefix $prefix -InterfaceIndex $InterfaceIndex `
                    -NextHop $Gateway -RouteMetric 5 -PolicyStore ActiveStore | Out-Null
            }
            $entries += @{ hostname = $hostname; address = $address; next_hop = $Gateway }
        }
    } catch {
        $failures += @{ hostname = $hostname; error = $_.Exception.Message }
    }
}
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'outputs') -Force | Out-Null
@{ gateway = $Gateway; interface_index = $InterfaceIndex; routes = $entries; failures = $failures;
   scope = 'ActiveStore IPv4 host routes; expires at reboot'; default_route_changed = $false } |
    ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $projectRoot 'outputs/download-routes.json') -Encoding utf8
if (-not ($entries | Where-Object { $_.hostname -eq 'huggingface.co' })) {
    throw 'No Hugging Face download route was configured.'
}
Write-Output "Configured $($entries.Count) model-download host routes via $Gateway."

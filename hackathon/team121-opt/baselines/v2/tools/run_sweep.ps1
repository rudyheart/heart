param(
    [Parameter(Mandatory = $true)]
    [string]$PlayerKitPath,
    [int]$StartSeed = 1,
    [int]$Count = 30
)

$ErrorActionPreference = 'Stop'
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$kit = Resolve-Path $PlayerKitPath
$logs = Join-Path $PSScriptRoot 'sweep-logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# Run this project's synthetic tests first.
Push-Location $projectRoot
try {
    python -m unittest discover tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Team121 strategy unit tests failed.' }
}
finally {
    Pop-Location
}

# Then run the official player-kit tests and matches.
Push-Location $kit
try {
    python -m unittest discover tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Official player-kit tests failed.' }

    for ($seed = $StartSeed; $seed -lt ($StartSeed + $Count); $seed++) {
        $a = Join-Path $logs "team121-seat0-seed-$seed.log"
        $b = Join-Path $logs "team121-seat1-seed-$seed.log"

        python -m engine team121 bot002 --seed $seed 2>&1 | Tee-Object -FilePath $a
        if ($LASTEXITCODE -ne 0) { throw "Seat-0 match failed for seed $seed" }

        python -m engine bot002 team121 --seed $seed 2>&1 | Tee-Object -FilePath $b
        if ($LASTEXITCODE -ne 0) { throw "Seat-1 match failed for seed $seed" }
    }

    Write-Host "Sweep complete. Logs: $logs"
}
finally {
    Pop-Location
}

param(
    [switch]$PersistPath
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bin = Join-Path $Root "bin"

if (-not (Test-Path -LiteralPath (Join-Path $Root "pyproject.toml"))) {
    throw "pyproject.toml not found. Run install.ps1 from the extracted Cortex package root."
}

$RequiredBins = @(
    "cortex-ctl.exe",
    "cortex-engine.exe",
    "cortex-watcher.exe",
    "cortex-mcp.exe"
)

foreach ($Name in $RequiredBins) {
    $Path = Join-Path $Bin $Name
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing required binary: $Path"
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not installed. Install it first: iwr -useb https://astral.sh/uv/install.ps1 | iex"
}

Push-Location $Root
try {
    uv sync
} finally {
    Pop-Location
}

$env:PATH = "$Bin;$env:PATH"

if ($PersistPath) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @()
    if ($UserPath) {
        $Entries = $UserPath -split ";"
    }
    if ($Entries -notcontains $Bin) {
        $NextPath = if ($UserPath) { "$UserPath;$Bin" } else { $Bin }
        [Environment]::SetEnvironmentVariable("Path", $NextPath, "User")
        Write-Host "Added to user PATH. Open a new terminal to use cortex-ctl globally."
    }
}

Write-Host "Cortex package installed for this terminal."
Write-Host "Package root: $Root"
Write-Host "Binary dir  : $Bin"
Write-Host ""
Write-Host "Try:"
Write-Host "  cortex-ctl status"

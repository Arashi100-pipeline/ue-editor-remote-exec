param(
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"
$skillRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $skillRoot "native\ue-remote-client\Cargo.toml"
$targetArguments = @()
$binaryRelative = "native\ue-remote-client\target\release\ue-remote-client.exe"
if ($Target) {
    $targetArguments = @("--target", $Target)
    $binaryRelative = "native\ue-remote-client\target\$Target\release\ue-remote-client.exe"
}
$binarySource = Join-Path $skillRoot $binaryRelative
$binaryDirectory = Join-Path $skillRoot "bin"
$binaryDestination = Join-Path $binaryDirectory "ue-remote-client.exe"
$checksumDestination = Join-Path $binaryDirectory "SHA256SUMS"
$remapFlags = @("--remap-path-prefix=$skillRoot=/build/source")
if ($env:USERPROFILE) {
    $remapFlags += "--remap-path-prefix=$env:USERPROFILE=/build/user"
}
$env:CARGO_ENCODED_RUSTFLAGS = $remapFlags -join [char]0x1f

& cargo build --release --locked @targetArguments --manifest-path $manifest
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Path $binaryDirectory -Force | Out-Null
Copy-Item -LiteralPath $binarySource -Destination $binaryDestination -Force
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $binaryDestination).Hash.ToLowerInvariant()
$checksumLine = "$hash  ue-remote-client.exe"
[System.IO.File]::WriteAllText(
    $checksumDestination,
    "$checksumLine`n",
    [System.Text.UTF8Encoding]::new($false)
)
Write-Output $checksumLine

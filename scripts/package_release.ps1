param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$')]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoPrefix = [System.IO.Path]::GetFullPath($repoRoot + [System.IO.Path]::DirectorySeparatorChar)
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "dist"))
if (-not $distRoot.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved dist directory escaped the repository root: $distRoot"
}

if (Test-Path -LiteralPath $distRoot) {
    Remove-Item -LiteralPath $distRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $distRoot | Out-Null

& (Join-Path $PSScriptRoot "build_native.ps1")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$packageName = "ue-editor-remote-exec-$Version-windows"
$distPrefix = [System.IO.Path]::GetFullPath(
    $distRoot + [System.IO.Path]::DirectorySeparatorChar
)
$stageRoot = [System.IO.Path]::GetFullPath((Join-Path $distRoot $packageName))
if (-not $stageRoot.StartsWith($distPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved staging directory escaped the dist directory: $stageRoot"
}
New-Item -ItemType Directory -Path $stageRoot | Out-Null

$files = @(
    ".gitattributes",
    ".gitignore",
    "about.toml",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "INSTALL.md",
    "LICENSE",
    "NOTICE",
    "OPEN_SOURCE_AUDIT.md",
    "PROTOCOL.md",
    "pyproject.toml",
    "README.md",
    "RELEASING.md",
    "SECURITY.md",
    "SKILL.md",
    "THIRD_PARTY_LICENSES.html",
    "THIRD_PARTY_NOTICES.md",
    "uv.lock"
)
$directories = @("bin", "native", "references", "scripts", "tests")

foreach ($file in $files) {
    $source = Join-Path $repoRoot $file
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Required release file is missing: $file"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $stageRoot $file)
}
foreach ($directory in $directories) {
    Copy-Item -LiteralPath (Join-Path $repoRoot $directory) -Destination $stageRoot -Recurse
}

$stagedTarget = [System.IO.Path]::GetFullPath(
    (Join-Path $stageRoot "native\ue-remote-client\target")
)
$stagePrefix = [System.IO.Path]::GetFullPath(
    $stageRoot + [System.IO.Path]::DirectorySeparatorChar
)
if (-not $stagedTarget.StartsWith($stagePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved target directory escaped the staging root: $stagedTarget"
}
if (Test-Path -LiteralPath $stagedTarget) {
    Remove-Item -LiteralPath $stagedTarget -Recurse -Force
}
Get-ChildItem -LiteralPath $stageRoot -Directory -Recurse -Filter "__pycache__" |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

$archive = Join-Path $distRoot "$packageName.zip"
Compress-Archive -LiteralPath $stageRoot -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$archive.sha256"
[System.IO.File]::WriteAllText(
    $checksumPath,
    "$hash  $([System.IO.Path]::GetFileName($archive))`n",
    [System.Text.UTF8Encoding]::new($false)
)

Remove-Item -LiteralPath $stageRoot -Recurse -Force

Write-Output $archive
Write-Output $checksumPath

[CmdletBinding()]
param(
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path (Split-Path -Parent $projectRoot) 'kt-data-platform-portable'
}

$outputRootFull = [IO.Path]::GetFullPath($OutputRoot)
$projectRootFull = [IO.Path]::GetFullPath($projectRoot)
$outputIsProject = $outputRootFull.Equals($projectRootFull, [StringComparison]::OrdinalIgnoreCase)
$outputIsInsideProject = $outputRootFull.StartsWith(
    $projectRootFull + [IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
)
if ($outputIsProject -or $outputIsInsideProject) {
    throw 'OutputRoot must be outside the source project.'
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$packageName = "kt-data-platform-$timestamp"
$packageDirectory = Join-Path $outputRootFull $packageName
$zipPath = Join-Path $outputRootFull "$packageName.zip"
if ((Test-Path -LiteralPath $packageDirectory) -or (Test-Path -LiteralPath $zipPath)) {
    throw "Package output already exists: $packageName"
}

New-Item -ItemType Directory -Path $outputRootFull -Force | Out-Null
New-Item -ItemType Directory -Path $packageDirectory | Out-Null

$runtimeDirectories = @('apps', 'config', 'fixtures', 'static', 'templates')
foreach ($relativePath in $runtimeDirectories) {
    $source = Join-Path $projectRootFull $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required runtime directory is missing: $relativePath"
    }
    Copy-Item -LiteralPath $source -Destination $packageDirectory -Recurse
}

$runtimeFiles = @(
    '.env.example',
    'manage.py',
    'requirements.txt',
    'run_local.bat',
    'START-HERE.txt'
)
foreach ($relativePath in $runtimeFiles) {
    $source = Join-Path $projectRootFull $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required runtime file is missing: $relativePath"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $packageDirectory $relativePath)
}

$packageScripts = Join-Path $packageDirectory 'scripts'
New-Item -ItemType Directory -Path $packageScripts | Out-Null
foreach ($scriptName in @('find_python.ps1', 'create_portable_package.ps1')) {
    $source = Join-Path $PSScriptRoot $scriptName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required package script is missing: $scriptName"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $packageScripts $scriptName)
}

$recipientReadme = @'
# KT Data Platform - Windows Portable Package

Requirement: Python 3.10+ or Anaconda/Miniconda. Internet access is needed for the first dependency installation.

1. Extract the complete ZIP to a normal writable folder. Do not run it inside the ZIP.
2. Double-click run_local.bat.
3. First start creates this computer's own .venv and a clean local database.
4. The browser opens http://127.0.0.1:8000/ after Django is ready.
5. Keep the command window open while using the site. Press Ctrl+C there to stop it.

Do not copy .venv from another computer. This package contains no source-computer database, accounts, experiments, uploads, or private configuration.
'@
Set-Content -LiteralPath (Join-Path $packageDirectory 'PORTABLE_WINDOWS_README.md') -Value $recipientReadme -Encoding UTF8

$cacheDirectories = @(
    Get-ChildItem -LiteralPath $packageDirectory -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('__pycache__', '.pytest_cache') } |
        Sort-Object { $_.FullName.Length } -Descending
)
foreach ($cacheDirectory in $cacheDirectories) {
    Remove-Item -LiteralPath $cacheDirectory.FullName -Recurse -Force
}
Get-ChildItem -LiteralPath $packageDirectory -File -Recurse -Force |
    Where-Object { $_.Extension -in @('.pyc', '.pyo') } |
    Remove-Item -Force

$forbiddenTopLevelPaths = @(
    '.venv', '.git', '.worktrees', 'db.sqlite3', 'media', 'staticfiles',
    '.env', '__pycache__', '.pytest_cache', 'tests'
)
foreach ($relativePath in $forbiddenTopLevelPaths) {
    if (Test-Path -LiteralPath (Join-Path $packageDirectory $relativePath)) {
        throw "Forbidden package content detected: $relativePath"
    }
}

$cachedArtifacts = @(
    Get-ChildItem -LiteralPath $packageDirectory -Recurse -Force |
        Where-Object {
            $_.Name -in @('__pycache__', '.pytest_cache') -or
            (-not $_.PSIsContainer -and $_.Extension -in @('.pyc', '.pyo'))
        }
)
if ($cachedArtifacts.Count -gt 0) {
    throw "Cached artifacts remain in package: $($cachedArtifacts.FullName -join ', ')"
}

Compress-Archive -Path (Join-Path $packageDirectory '*') -DestinationPath $zipPath -CompressionLevel Optimal

Write-Output "PACKAGE_DIR=$packageDirectory"
Write-Output "PACKAGE_ZIP=$zipPath"

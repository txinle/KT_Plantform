[CmdletBinding()]
param(
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
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
$tarGzPath = Join-Path $outputRootFull "$packageName.tar.gz"
if (
    (Test-Path -LiteralPath $packageDirectory) -or
    (Test-Path -LiteralPath $zipPath) -or
    (Test-Path -LiteralPath $tarGzPath)
) {
    throw "Package output already exists: $packageName"
}

New-Item -ItemType Directory -Path $outputRootFull -Force | Out-Null
New-Item -ItemType Directory -Path $packageDirectory | Out-Null

$excludedDirectories = @(
    '.venv', 'node_modules', '.git', '.worktrees', '__pycache__', '.pytest_cache',
    'dist', 'coverage', '.e2e-results', 'playwright-report', 'media', 'staticfiles',
    'private', '.private', 'secrets'
)

function Copy-PackageTree {
    param([string]$Source, [string]$Destination)

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        if ($item.PSIsContainer) {
            if ($item.Name -notin $excludedDirectories) {
                Copy-PackageTree -Source $item.FullName -Destination (Join-Path $Destination $item.Name)
            }
        }
        elseif ($item.Name -eq '.env.example' -or (
            $item.Name -notlike '.env*' -and
            $item.Name -notlike '*.env' -and
            $item.Extension -notin @('.pyc', '.pyo', '.sqlite3', '.db', '.log', '.key', '.pem', '.pfx')
        )) {
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name)
        }
    }
}

$runtimeDirectories = @('server/apps', 'server/config', 'server/fixtures', 'server/static', 'server/templates', 'client', 'contracts')
foreach ($relativePath in $runtimeDirectories) {
    $source = Join-Path $projectRootFull $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required runtime directory is missing: $relativePath"
    }
    Copy-PackageTree -Source $source -Destination (Join-Path $packageDirectory $relativePath)
}

$runtimeFiles = @(
    '.env.example',
    'server/manage.py',
    'server/requirements.txt',
    'run_local.bat',
    'run_local.sh',
    'START-HERE.txt',
    'README.md'
)
foreach ($relativePath in $runtimeFiles) {
    $source = Join-Path $projectRootFull $relativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required runtime file is missing: $relativePath"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $packageDirectory $relativePath)
}

$packageScripts = Join-Path $packageDirectory 'server/scripts'
New-Item -ItemType Directory -Path $packageScripts | Out-Null
foreach ($scriptName in @(
    'find_python.ps1',
    'run_dev.py',
    'create_portable_package.ps1',
    'create_portable_archive.py'
)) {
    $source = Join-Path $PSScriptRoot $scriptName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required package script is missing: $scriptName"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $packageScripts $scriptName)
}

$recipientReadme = @'
# KT Data Platform - Windows Portable Package

Requirements: Python 3.13, Node.js 24 and PostgreSQL 17. Internet access is needed for the first dependency installation.

1. Extract the complete ZIP to a normal writable folder. Do not run it inside the ZIP.
2. Double-click run_local.bat.
3. First start creates this computer's own server/.venv and installs client dependencies. Configure the project's PostgreSQL instance before starting.
4. The browser opens http://127.0.0.1:8000/ after Django is ready.
5. Keep the command window open while using the site. Press Ctrl+C there to stop it.

Keep server, client and contracts together with the root launchers. Do not copy .venv or node_modules from another computer. This package contains no source-computer database, accounts, experiments, uploads, or private configuration.
'@
Set-Content -LiteralPath (Join-Path $packageDirectory 'PORTABLE_WINDOWS_README.md') -Value $recipientReadme -Encoding UTF8

$unixReadme = @'
# KT Data Platform - Linux/macOS Portable Package

Requirements: Python 3.13 with venv and ensurepip, Node.js 24 and PostgreSQL 17. Internet access is needed for the first dependency installation.

1. Extract the complete .tar.gz to a writable folder.
2. Open a terminal in that folder and run ./run_local.sh.
3. First start creates this computer's own server/.venv and installs client dependencies. Configure the project's PostgreSQL instance before starting.
4. The browser opens http://127.0.0.1:8000/ after Django is ready.
5. Keep the terminal open while using the site. Press Ctrl+C to stop it.

This launcher uses Django's development server. Do not expose it directly to the public internet.
'@
Set-Content -LiteralPath (Join-Path $packageDirectory 'PORTABLE_UNIX_README.md') -Value $unixReadme -Encoding UTF8

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
            $_.Name -in $excludedDirectories -or
            (-not $_.PSIsContainer -and $_.Extension -in @('.pyc', '.pyo'))
        }
)
if ($cachedArtifacts.Count -gt 0) {
    throw "Cached artifacts remain in package: $($cachedArtifacts.FullName -join ', ')"
}

Compress-Archive -Path (Join-Path $packageDirectory '*') -DestinationPath $zipPath -CompressionLevel Optimal

$finderOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'find_python.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.10+ is required to create the Unix archive.'
}
$archivePython = @($finderOutput)[0].Trim()
& $archivePython (Join-Path $PSScriptRoot 'create_portable_archive.py') --source $packageDirectory --output $tarGzPath
if ($LASTEXITCODE -ne 0) {
    throw 'Unix archive creation failed.'
}

Write-Output "PACKAGE_DIR=$packageDirectory"
Write-Output "PACKAGE_ZIP=$zipPath"
Write-Output "PACKAGE_TAR_GZ=$tarGzPath"

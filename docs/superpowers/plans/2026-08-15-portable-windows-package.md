# Portable Windows Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a clean Windows ZIP that recipients with Python or Anaconda can extract, double-click, and use without machine-specific paths or transferred research data.

**Architecture:** Keep `run_local.bat` as the user entry point, delegate machine-independent Python discovery to `scripts/find_python.ps1`, and build packages with an allowlist in `scripts/create_portable_package.ps1`. The package omits local environments, databases, uploads, secrets, caches, and workspace files; first launch creates a recipient-local `.venv` and clean SQLite database.

**Tech Stack:** Windows batch, Windows PowerShell 5+, Python 3.10+, Django 5.2, ZIP/Compress-Archive

---

### Task 1: Add machine-independent Python discovery

**Files:**
- Create: `scripts/find_python.ps1`

- [ ] **Step 1: Run the failing discovery contract**

```powershell
$finder = '.\scripts\find_python.ps1'
if (-not (Test-Path -LiteralPath $finder)) {
    throw 'Python discovery helper is missing.'
}
```

Expected: FAIL with `Python discovery helper is missing.`

- [ ] **Step 2: Implement the discovery helper**

Create `scripts/find_python.ps1`:

```powershell
[CmdletBinding()]
param(
    [string[]]$CandidatePaths
)

$ErrorActionPreference = 'Stop'
$minimumVersion = [Version]'3.10'
$candidates = New-Object 'System.Collections.Generic.List[string]'
$seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)

function Add-Candidate {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if ($expanded -match '\\WindowsApps\\python(?:\.exe)?$') {
        return
    }

    if (Test-Path -LiteralPath $expanded -PathType Leaf) {
        $resolved = (Resolve-Path -LiteralPath $expanded).Path
        if ($seen.Add($resolved)) {
            [void]$candidates.Add($resolved)
        }
    }
}

if ($CandidatePaths -and $CandidatePaths.Count -gt 0) {
    foreach ($candidate in $CandidatePaths) {
        Add-Candidate $candidate
    }
}
else {
    foreach ($commandName in @('py.exe', 'python.exe')) {
        foreach ($command in @(Get-Command $commandName -All -ErrorAction SilentlyContinue)) {
            Add-Candidate $command.Source
        }
    }

    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE 'Anaconda3\python.exe'),
        (Join-Path $env:USERPROFILE 'Miniconda3\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'anaconda3\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'miniconda3\python.exe'),
        (Join-Path $env:ProgramData 'Anaconda3\python.exe'),
        (Join-Path $env:ProgramData 'Miniconda3\python.exe')
    )) {
        Add-Candidate $candidate
    }

    foreach ($baseDirectory in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        $env:ProgramFiles
    )) {
        if (-not (Test-Path -LiteralPath $baseDirectory -PathType Container)) {
            continue
        }
        foreach ($directory in @(Get-ChildItem -LiteralPath $baseDirectory -Directory -Filter 'Python*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
            Add-Candidate (Join-Path $directory.FullName 'python.exe')
        }
    }
}

foreach ($candidate in $candidates) {
    & $candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
        [Console]::Out.WriteLine($candidate)
        exit 0
    }
}

[Console]::Error.WriteLine("No usable Python $minimumVersion+ installation was found.")
exit 1
```

- [ ] **Step 3: Verify discovery through a simulated Explorer environment**

```powershell
$originalPath = $env:Path
try {
    $env:Path = (
        [Environment]::GetEnvironmentVariable('Path', 'Machine').TrimEnd(';') +
        ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User').TrimStart(';')
    )
    $pythonPath = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\find_python.ps1
    if ($LASTEXITCODE -ne 0) { throw 'Discovery failed.' }
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Invalid result: $pythonPath" }
    & $pythonPath -c "import sys; print(sys.executable); print(sys.version_info[:2])"
}
finally {
    $env:Path = $originalPath
}
```

Expected: exit code `0`, a real interpreter path, and Python version at least 3.10 even though the active Conda PATH was removed.

- [ ] **Step 4: Verify the missing-Python path**

```powershell
$missing = Join-Path $env:TEMP 'kt-python-does-not-exist\python.exe'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\find_python.ps1 -CandidatePaths $missing
if ($LASTEXITCODE -eq 0) { throw 'Missing-Python test unexpectedly passed.' }
```

Expected: non-zero exit and `No usable Python 3.10+ installation was found.`

- [ ] **Step 5: Commit Python discovery**

```powershell
git add scripts/find_python.ps1
git commit -m "feat: add portable Windows Python discovery"
```

### Task 2: Integrate discovery and harden the launcher

**Files:**
- Modify: `run_local.bat:1-57`

- [ ] **Step 1: Run the failing portable-launcher contract**

```powershell
$script = Get-Content -Raw .\run_local.bat
$checks = [ordered]@{
    uses_discovery_helper = $script -match 'scripts\\find_python\.ps1'
    no_user_python_path = $script -notmatch '%USERPROFILE%\\(?:Ana|Mini)conda3'
    no_makemigrations = $script -notmatch 'manage\.py makemigrations'
    port_conflict_check = $script -match 'Get-NetTCPConnection'
    relative_venv = $script -match 'set "VENV_PYTHON=\.venv\\Scripts\\python\.exe"'
}
$failed = @($checks.GetEnumerator() | Where-Object { -not $_.Value } | ForEach-Object Key)
if ($failed.Count) { throw "Portable launcher contract failed: $($failed -join ', ')" }
```

Expected: FAIL for discovery-helper integration, fixed Anaconda assumptions, runtime migration generation, and port-conflict handling.

- [ ] **Step 2: Replace the launcher implementation**

Replace `run_local.bat` with:

```bat
@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "APP_URL=http://127.0.0.1:8000/"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "FIRST_RUN=0"
if not exist db.sqlite3 set "FIRST_RUN=1"

if exist ".venv" if not exist "%VENV_PYTHON%" (
    echo [1/4] Removing an incomplete local Python environment...
    rmdir /s /q ".venv"
    if errorlevel 1 goto :fail
)

if not exist "%VENV_PYTHON%" (
    echo [1/4] Discovering Python and creating the local environment...
    set "BOOTSTRAP_PYTHON="
    for /f "delims=" %%P in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\find_python.ps1"') do set "BOOTSTRAP_PYTHON=%%P"
    if not defined BOOTSTRAP_PYTHON (
        echo [ERROR] Python 3.10 or newer was not found.
        echo Install Python or Anaconda, then double-click run_local.bat again.
        goto :fail
    )
    "!BOOTSTRAP_PYTHON!" -m venv .venv
    if errorlevel 1 goto :fail
    "%VENV_PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto :fail
)

echo [2/4] Checking Python dependencies...
"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/4] Checking the database...
"%VENV_PYTHON%" manage.py migrate --noinput
if errorlevel 1 goto :fail
if "%FIRST_RUN%"=="1" (
    "%VENV_PYTHON%" manage.py seed_demo
    if errorlevel 1 goto :fail
)

powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Port 8000 is already in use. Stop the existing local server first.
    goto :fail
)

echo [4/4] Starting the local website...
echo Website: %APP_URL%
echo Ctrl+click the URL above if the browser does not open automatically.
if /I not "%KT_SKIP_BROWSER%"=="1" (
    start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='%APP_URL%'; for ($attempt = 0; $attempt -lt 60; $attempt++) { try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1 | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Milliseconds 500 } }; exit 1"
)
"%VENV_PYTHON%" manage.py runserver 0.0.0.0:8000
exit /b %errorlevel%

:fail
echo.
echo [ERROR] Local startup failed. Review the message above.
pause
exit /b 1
```

- [ ] **Step 3: Re-run the portable-launcher contract**

Run the Step 1 assertion again.

Expected: all five checks pass.

- [ ] **Step 4: Verify the launcher resolves Python under simulated Explorer PATH**

Run only when port 8000 is free:

```powershell
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$originalPath = $env:Path
try {
    $env:Path = $machinePath.TrimEnd(';') + ';' + $userPath.TrimStart(';')
    $env:KT_SKIP_BROWSER = '1'
    .\run_local.bat
}
finally {
    $env:Path = $originalPath
    Remove-Item Env:\KT_SKIP_BROWSER -ErrorAction SilentlyContinue
}
```

Expected: the launcher uses or creates the project `.venv`, prints the local URL, and starts Django. Confirm HTTP 200 from a second terminal, then stop the exact launcher with Ctrl+C.

- [ ] **Step 5: Commit launcher integration**

```powershell
git add run_local.bat
git commit -m "fix: make Windows launcher machine independent"
```

### Task 3: Create the clean portable-package builder

**Files:**
- Create: `scripts/create_portable_package.ps1`

- [ ] **Step 1: Run the failing package-builder contract**

```powershell
if (-not (Test-Path -LiteralPath '.\scripts\create_portable_package.ps1')) {
    throw 'Portable package builder is missing.'
}
```

Expected: FAIL with `Portable package builder is missing.`

- [ ] **Step 2: Implement the allowlist package builder**

Create `scripts/create_portable_package.ps1`:

```powershell
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
if ($outputRootFull.StartsWith($projectRootFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
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
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $scriptName) -Destination (Join-Path $packageScripts $scriptName)
}

$recipientReadme = @'
# KT Data Platform - Windows Portable Package

Prerequisite: install Python 3.10+ or Anaconda/Miniconda and keep internet access for the first dependency installation.

1. Extract the complete ZIP to a normal writable folder.
2. Double-click run_local.bat.
3. The first start creates this computer's .venv and a clean SQLite database.
4. The browser opens http://127.0.0.1:8000/ after Django is ready.
5. Keep the terminal open while using the website; press Ctrl+C to stop it.

Do not copy .venv from another computer. This package intentionally contains no source-computer database, uploads, accounts, experiments, or secrets.
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

$forbiddenPaths = @(
    '.venv', '.git', '.worktrees', 'db.sqlite3', 'media', 'staticfiles',
    '.env', '__pycache__', 'tests'
)
foreach ($relativePath in $forbiddenPaths) {
    if (Test-Path -LiteralPath (Join-Path $packageDirectory $relativePath)) {
        throw "Forbidden package content detected: $relativePath"
    }
}

Compress-Archive -Path (Join-Path $packageDirectory '*') -DestinationPath $zipPath -CompressionLevel Optimal

Write-Output "PACKAGE_DIR=$packageDirectory"
Write-Output "PACKAGE_ZIP=$zipPath"
```

- [ ] **Step 3: Build into an output path containing spaces**

```powershell
$testOutput = Join-Path $env:TEMP 'KT Portable Package Test'
$result = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\create_portable_package.ps1 -OutputRoot $testOutput
$packageDirectory = ($result | Where-Object { $_ -like 'PACKAGE_DIR=*' }) -replace '^PACKAGE_DIR=', ''
$zipPath = ($result | Where-Object { $_ -like 'PACKAGE_ZIP=*' }) -replace '^PACKAGE_ZIP=', ''
if (-not (Test-Path -LiteralPath $packageDirectory -PathType Container)) { throw 'Package directory missing.' }
if (-not (Test-Path -LiteralPath $zipPath -PathType Leaf)) { throw 'Package ZIP missing.' }
```

Expected: a timestamped directory and ZIP are created outside the source tree.

- [ ] **Step 4: Audit required and forbidden contents**

```powershell
$required = @(
    'apps', 'config', 'fixtures', 'static', 'templates',
    'scripts\find_python.ps1', 'manage.py', 'requirements.txt',
    'run_local.bat', 'PORTABLE_WINDOWS_README.md'
)
foreach ($relativePath in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $packageDirectory $relativePath))) {
        throw "Required package content missing: $relativePath"
    }
}
$forbidden = @('.venv', '.git', '.worktrees', 'db.sqlite3', 'media', 'staticfiles', '.env', 'tests')
foreach ($relativePath in $forbidden) {
    if (Test-Path -LiteralPath (Join-Path $packageDirectory $relativePath)) {
        throw "Forbidden package content present: $relativePath"
    }
}
$nestedCaches = @(
    Get-ChildItem -LiteralPath $packageDirectory -Recurse -Force |
        Where-Object { $_.Name -in @('__pycache__', '.pytest_cache') -or $_.Extension -in @('.pyc', '.pyo') }
)
if ($nestedCaches.Count) {
    throw "Cached Python artifacts are present: $($nestedCaches.FullName -join ', ')"
}
```

Expected: every required item exists and every forbidden item is absent.

- [ ] **Step 5: Commit the package builder**

```powershell
git add scripts/create_portable_package.ps1
git commit -m "feat: add clean Windows package builder"
```

### Task 4: Verify a clean extracted package end to end

**Files:**
- Verify: generated package only
- Verify: repository regression suite

- [ ] **Step 1: Extract the generated ZIP to a path containing spaces**

```powershell
$extractRoot = Join-Path $env:TEMP 'KT Extracted Package Test'
$extractDirectory = Join-Path $extractRoot 'KT Platform'
New-Item -ItemType Directory -Path $extractDirectory -Force | Out-Null
Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDirectory
```

Expected: source files appear directly under `$extractDirectory`.

- [ ] **Step 2: Re-audit the extracted package**

```powershell
if (Test-Path -LiteralPath (Join-Path $extractDirectory '.venv')) { throw 'Extracted package contains .venv.' }
if (Test-Path -LiteralPath (Join-Path $extractDirectory 'db.sqlite3')) { throw 'Extracted package contains a database.' }
$finderResult = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $extractDirectory 'scripts\find_python.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Extracted Python discovery failed.' }
```

Expected: no environment or database is present, and Python discovery succeeds from the extracted copy.

- [ ] **Step 3: Perform first-start integration**

Before this step, verify port 8000 is free. If another user-owned server is listening, stop and request that the user close it; do not kill it.

From `$extractDirectory`, run `run_local.bat` with `KT_SKIP_BROWSER=1` in a managed terminal session. Wait for the local URL, then run:

```powershell
$response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/' -MaximumRedirection 5 -TimeoutSec 5
if ($response.StatusCode -ne 200) { throw "Unexpected HTTP status: $($response.StatusCode)" }
if (-not (Test-Path -LiteralPath (Join-Path $extractDirectory '.venv\Scripts\python.exe'))) { throw 'First start did not create .venv.' }
if (-not (Test-Path -LiteralPath (Join-Path $extractDirectory 'db.sqlite3'))) { throw 'First start did not create the database.' }
```

Expected: HTTP 200, a new extracted-package `.venv`, and a new extracted-package `db.sqlite3`. Stop only the managed test launcher.

- [ ] **Step 4: Verify the source repository**

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
node --check static\js\app.js
node --check static\js\assessment.js
git diff --check
```

Expected: no Django issues, all 48 or more tests pass, static collection succeeds, both JavaScript files parse, and no whitespace errors are reported.

- [ ] **Step 5: Remove only verified temporary outputs**

Resolve `$testOutput` and `$extractRoot` to absolute paths, verify both are direct children of `[IO.Path]::GetFullPath($env:TEMP)`, then remove those two temporary directories recursively in PowerShell. Do not delete any source-project path.

- [ ] **Step 6: Review final scope**

```powershell
git status --short
git log -5 --oneline
```

Expected: only the user's pre-existing `README.md` modification remains uncommitted; implementation commits contain the finder, launcher, and package builder.

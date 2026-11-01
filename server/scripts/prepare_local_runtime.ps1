[CmdletBinding()]
param([string]$RuntimeRoot)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Compatible([string]$path, [string]$kind) {
    if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    try {
        if ($kind -eq 'python') {
            $value = & $path -c "import ensurepip, ssl, sys, venv; print('%d.%d' % sys.version_info[:2])" 2>$null
            return $LASTEXITCODE -eq 0 -and "$value".Trim() -eq '3.13'
        }
        $value = & $path --version 2>$null
        if ($LASTEXITCODE -ne 0) {
            if ($kind -eq 'node') { throw "Node version check failed (exit $LASTEXITCODE): $path" }
            return $false
        }
        if ($kind -eq 'node') { return "$value" -match '^v(\d+)\.' -and [int]$Matches[1] -ge 24 -and (Test-Path -LiteralPath (Join-Path (Split-Path $path -Parent) 'npm.cmd')) }
        return "$value" -match 'PostgreSQL\)?\s+17\.'
    } catch {
        if ($kind -eq 'node') { throw "Cannot check existing Node; download stopped. $($_.Exception.Message)" }
        return $false
    }
}

function Digest([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}

function Package($kind, $directory, $executable, $url, $hash, $archiveRoot, $candidates) {
    $target = Join-Path $RuntimeRoot $directory
    $orderedCandidates = @((Join-Path $target $executable)) + @($candidates)
    if ($kind -eq 'node') { $orderedCandidates = @($candidates) + @((Join-Path $target $executable)) }
    foreach ($candidate in $orderedCandidates) {
        if (Compatible $candidate $kind) { Write-Host "[runtime] Reusing ${kind}: $candidate"; return $candidate }
    }
    $archive = Join-Path $RuntimeRoot ('downloads\' + [IO.Path]::GetFileName(([Uri]$url).AbsolutePath))
    [void][IO.Directory]::CreateDirectory((Split-Path $archive -Parent))
    if (-not (Test-Path -LiteralPath $archive) -or (Digest $archive) -ne $hash) {
        $partial = "$archive.partial"
        Write-Host "[runtime] Downloading $kind..."
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $partial -TimeoutSec 180
            if ((Digest $partial) -ne $hash) { throw "SHA-256 verification failed for $kind; nothing was installed." }
            Move-Item -LiteralPath $partial -Destination $archive -Force
        } finally { if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force } }
    }
    $stage = Join-Path $RuntimeRoot ('.staging\' + [Guid]::NewGuid().ToString('N'))
    try {
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $stage)
        $source = if ($archiveRoot) { Join-Path $stage $archiveRoot } else { $stage }
        if (-not (Compatible (Join-Path $source $executable) $kind)) { throw "Downloaded $kind is incompatible." }
        if (Test-Path -LiteralPath $target) {
            if (-not ([IO.Path]::GetFullPath($target)).StartsWith($RuntimeRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected package path.' }
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        Move-Item -LiteralPath $source -Destination $target
    } finally {
        if (-not ([IO.Path]::GetFullPath($stage)).StartsWith($RuntimeRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected staging path.' }
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    }
    return (Join-Path $target $executable)
}

try {
    if (-not $RuntimeRoot) { $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'KT——platform\runtime' }
    $RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
    $project = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $venv = Join-Path $project '.venv'
    foreach ($part in @('cache\pip','cache\npm','cache\temp')) { [void][IO.Directory]::CreateDirectory((Join-Path $RuntimeRoot $part)) }
    $pythonCandidates = @((Join-Path $venv 'Scripts\python.exe'))
    if (-not (Compatible $pythonCandidates[0] 'python')) {
        $found = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'find_python.ps1')
        if ($LASTEXITCODE -eq 0) { $pythonCandidates += $found }
    }
    # Python.org's Windows release manifest publishes this full-runtime ZIP hash.
    $python = Package 'python' 'python-3.13.17' 'python.exe' 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.zip' 'b26ca4b4ee51e157836049934d2b8eabd55939be296f3130254251b6df96ec2f' '' $pythonCandidates
    $nodeCandidates = @()
    if ($env:KT_NODE_DIR) { $nodeCandidates += Join-Path $env:KT_NODE_DIR 'node.exe' }
    $found = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($found) { $nodeCandidates += $found.Source }
    # Node.js v24.20.0/SHASUMS256.txt.
    $node = Package 'node' 'node-v24.20.0-win-x64' 'node.exe' 'https://nodejs.org/dist/v24.20.0/node-v24.20.0-win-x64.zip' '6cac9ffbca8f6a47091e4b5c772e0606049c3871cb67d900c0cedde630e545ba' 'node-v24.20.0-win-x64' $nodeCandidates
    $pgCandidates = @()
    $found = Get-Command pg_isready.exe -ErrorAction SilentlyContinue
    if ($found) { $pgCandidates += $found.Source }
    # EDB's revision-3 checksum: github.com/EnterpriseDB/edb-installers/issues/682.
    $postgres = Package 'postgres' 'PostgreSQL-17.11-portable' 'pgsql\bin\pg_isready.exe' 'https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip' '4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf' '' $pgCandidates
    $pgBin = Split-Path $postgres -Parent
    $pgData = Join-Path $RuntimeRoot 'PostgreSQL-17.11-data'
    $versionFile = Join-Path $pgData 'PG_VERSION'
    if ((Test-Path -LiteralPath $versionFile) -and (Get-Content -LiteralPath $versionFile -Raw).Trim() -ne '17') { throw 'PostgreSQL data version must be 17; existing data was not modified.' }
    $env:PGPASSWORD = $env:POSTGRES_PASSWORD
    $env:PGCONNECT_TIMEOUT = '5'
    & $postgres -h $env:POSTGRES_HOST -p $env:POSTGRES_PORT -U $env:POSTGRES_USER *> $null
    if ($LASTEXITCODE -ne 0) {
        if ($env:POSTGRES_HOST -notin @('127.0.0.1','localhost')) { throw 'The configured remote PostgreSQL server is unavailable.' }
        if (-not (Test-Path -LiteralPath $versionFile)) {
            if ((Test-Path -LiteralPath $pgData) -and @(Get-ChildItem -LiteralPath $pgData -Force).Count) { throw 'Existing nonempty PostgreSQL data was not modified.' }
            $passwordFile = Join-Path $RuntimeRoot ('cache\temp\postgres-' + [Guid]::NewGuid().ToString('N') + '.txt')
            try {
                [IO.File]::WriteAllText($passwordFile, $env:POSTGRES_PASSWORD + "`n", (New-Object Text.UTF8Encoding($false)))
                & (Join-Path $pgBin 'initdb.exe') -D $pgData -U $env:POSTGRES_USER --encoding=UTF8 --locale=C --auth=scram-sha-256 "--pwfile=$passwordFile"
                if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL initialization failed.' }
            } finally { Remove-Item -LiteralPath $passwordFile -Force }
        }
        # local_postgres.py isolates pg_ctl.exe from Ctrl+C and waits for recovery.
        & $python -X utf8 (Join-Path $PSScriptRoot 'local_postgres.py') --bin $pgBin --data $pgData --host $env:POSTGRES_HOST --port $env:POSTGRES_PORT --user $env:POSTGRES_USER
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed.' }
    }
    $connection = @('-X','-w','-h',$env:POSTGRES_HOST,'-p',$env:POSTGRES_PORT,'-U',$env:POSTGRES_USER,'-d','postgres','-A','-t')
    $version = & (Join-Path $pgBin 'psql.exe') @connection -c 'SHOW server_version_num;'
    if ($LASTEXITCODE -ne 0 -or [int]"$version" -lt 170000 -or [int]"$version" -ge 180000) { throw 'The configured PostgreSQL connection must be version 17 with valid credentials.' }
    $dbName = $env:POSTGRES_DB.Replace("'", "''")
    $exists = & (Join-Path $pgBin 'psql.exe') @connection -c "SELECT 1 FROM pg_database WHERE datname = '$dbName';"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the configured database.' }
    if ("$exists".Trim() -ne '1') {
        & (Join-Path $pgBin 'createdb.exe') -h $env:POSTGRES_HOST -p $env:POSTGRES_PORT -U $env:POSTGRES_USER -- $env:POSTGRES_DB
        if ($LASTEXITCODE -ne 0) { throw 'Database creation failed.' }
    }
    if (-not (Compatible (Join-Path $venv 'Scripts\python.exe') 'python')) {
        if (Test-Path -LiteralPath $venv) {
            if ([IO.Path]::GetFullPath($venv) -ne [IO.Path]::GetFullPath((Join-Path $project '.venv'))) { throw 'Unexpected virtual environment path.' }
            Remove-Item -LiteralPath $venv -Recurse -Force
        }
        & $python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create the project Python environment.' }
    }
    $values = [ordered]@{KT_TOOLING_ROOT=$RuntimeRoot; KT_NODE_DIR=(Split-Path $node -Parent); KT_POSTGRES_BIN=$pgBin; KT_POSTGRES_DATA=$pgData; PLAYWRIGHT_BROWSERS_PATH=(Join-Path $RuntimeRoot 'pw-fresh-browsers')}
    $lines = @('@echo off')
    foreach ($name in $values.Keys) { $lines += 'set "' + $name + '=' + $values[$name] + '"' }
    [IO.File]::WriteAllText((Join-Path $RuntimeRoot 'environment.cmd'), ($lines -join "`r`n") + "`r`n", (New-Object Text.UTF8Encoding($false)))
    Write-Host "[runtime] Ready: $RuntimeRoot"
} catch { [Console]::Error.WriteLine("[ERROR] $($_.Exception.Message)"); exit 1 }

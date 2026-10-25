[CmdletBinding()]
param([switch]$Help)

$ErrorActionPreference = 'Stop'
$serverDirectory = Join-Path $PSScriptRoot 'server'
$pythonCandidates = @(
    (Join-Path $serverDirectory '.venv\Scripts\python.exe'),
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')
)
$pythonExecutable = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExecutable) {
    throw 'No Python environment exists in this checkout. Run run_local.bat first.'
}
$defaults = @{
    POSTGRES_HOST = '127.0.0.1'
    POSTGRES_PORT = '55432'
    POSTGRES_USER = 'kt_user'
    POSTGRES_PASSWORD = 'kt_password'
    POSTGRES_DB = 'kt_platform'
}
foreach ($name in $defaults.Keys) {
    if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
        [Environment]::SetEnvironmentVariable($name, $defaults[$name], 'Process')
    }
}
Write-Host "Database: $env:POSTGRES_HOST`:$env:POSTGRES_PORT / $env:POSTGRES_DB"
$previousOutputEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Push-Location $serverDirectory
try {
    if ($Help) { & $pythonExecutable -X utf8 manage.py add_admin --help }
    else { & $pythonExecutable -X utf8 manage.py add_admin }
    $commandExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    [Console]::OutputEncoding = $previousOutputEncoding
}
exit $commandExitCode

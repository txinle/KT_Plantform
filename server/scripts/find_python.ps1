[CmdletBinding()]
param(
    [string[]]$CandidatePaths
)

$ErrorActionPreference = 'Stop'
$minimumVersion = [Version]'3.13'
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

function Join-ExistingBasePath {
    param(
        [string]$BasePath,
        [string]$ChildPath
    )

    if (-not [string]::IsNullOrWhiteSpace($BasePath)) {
        Join-Path $BasePath $ChildPath
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

    $commonCandidates = @(
        (Join-ExistingBasePath $env:USERPROFILE 'Anaconda3\python.exe'),
        (Join-ExistingBasePath $env:USERPROFILE 'Miniconda3\python.exe'),
        (Join-ExistingBasePath $env:LOCALAPPDATA 'anaconda3\python.exe'),
        (Join-ExistingBasePath $env:LOCALAPPDATA 'miniconda3\python.exe'),
        (Join-ExistingBasePath $env:ProgramData 'Anaconda3\python.exe'),
        (Join-ExistingBasePath $env:ProgramData 'Miniconda3\python.exe')
    )
    foreach ($candidate in $commonCandidates) {
        Add-Candidate $candidate
    }

    $pythonRoots = @(
        (Join-ExistingBasePath $env:LOCALAPPDATA 'Programs\Python'),
        $env:ProgramFiles
    )
    foreach ($baseDirectory in $pythonRoots) {
        if ([string]::IsNullOrWhiteSpace($baseDirectory) -or
            -not (Test-Path -LiteralPath $baseDirectory -PathType Container)) {
            continue
        }

        $directories = Get-ChildItem -LiteralPath $baseDirectory -Directory -Filter 'Python*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($directory in @($directories)) {
            Add-Candidate (Join-Path $directory.FullName 'python.exe')
        }
    }
}

foreach ($candidate in $candidates) {
    $versionArgument = @()
    if ([IO.Path]::GetFileName($candidate) -eq 'py.exe') { $versionArgument = @('-3.13') }
    $executable = & $candidate @versionArgument -c "import ensurepip, os, sys, venv; print(sys.executable) if sys.version_info[:2] == (3, 13) and os.path.isfile(sys.executable) else sys.exit(1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        [Console]::Out.WriteLine($executable)
        exit 0
    }
}

[Console]::Error.WriteLine("No usable Python $minimumVersion installation was found.")
exit 1

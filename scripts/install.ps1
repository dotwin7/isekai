[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$Ref,

    [Alias("Path")]
    [string]$ProjectPath = ".",

    [ValidateSet("all", "codex", "claude", "kiro")]
    [string[]]$Runtime = @("all"),

    [switch]$AdoptFoundation,
    [switch]$Init,
    [string[]]$Profile = @(),
    [string]$Python
)

$ErrorActionPreference = "Stop"

function Assert-LastExitCode {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE"
    }
}

if ($Source.StartsWith("-")) {
    throw "Source cannot start with '-'"
}
if ($Source -match "^[A-Za-z][A-Za-z0-9+.-]*::") {
    throw "Source must not use a Git transport helper"
}
if ($Source.Contains("://")) {
    try {
        $sourceUri = [Uri]::new($Source, [UriKind]::Absolute)
    }
    catch {
        throw "Source must be a valid absolute Git URL"
    }
    $httpSource = $sourceUri.Scheme -in @("http", "https")
    $hasUserInfo = -not [string]::IsNullOrEmpty($sourceUri.UserInfo)
    $httpAuthorityHasUserInfo = $Source -match "^[Hh][Tt][Tt][Pp][Ss]?://[^/]*@"
    if (($httpSource -and ($hasUserInfo -or $httpAuthorityHasUserInfo)) -or $sourceUri.UserInfo.Contains(":")) {
        throw "Source must not contain embedded credentials"
    }
    if ($sourceUri.Query -or $sourceUri.Fragment) {
        throw "Source must not contain a query or fragment"
    }
}
if ($Ref.StartsWith("-")) {
    throw "Ref cannot start with '-'"
}
if ($Profile.Count -gt 0 -and -not $Init) {
    throw "-Profile requires -Init"
}
if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Project directory does not exist: $ProjectPath"
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is required"
}

$pythonPrefix = @()
if ($Python) {
    $pythonCommand = Get-Command $Python -ErrorAction Stop
}
else {
    $pythonCommand = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $pythonPrefix = @("-3")
        }
    }
    if (-not $pythonCommand) {
        throw "Python 3.11 or newer is required"
    }
}
$pythonExecutable = $pythonCommand.Source

& $pythonExecutable @pythonPrefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
Assert-LastExitCode "Python version check"

$resolvedProject = (Resolve-Path -LiteralPath $ProjectPath).Path
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("isekai-bootstrap-" + [guid]::NewGuid().ToString("N"))
$checkout = Join-Path $temporaryRoot "release"
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
$previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")

try {
    & git clone --quiet --no-checkout -- $Source $checkout
    Assert-LastExitCode "Git clone"
    $resolvedCommit = $null
    if ($Ref -match "^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$") {
        $resolvedCommit = (& git -C $checkout rev-parse --verify "${Ref}^{commit}")
        Assert-LastExitCode "Resolve immutable Git commit"
        if ($resolvedCommit.Trim().ToLowerInvariant() -ne $Ref.ToLowerInvariant()) {
            throw "Git ref is not the requested full commit: $Ref"
        }
    }
    else {
        $resolvedCommit = (& git -C $checkout rev-parse --verify "refs/tags/${Ref}^{commit}")
        if ($LASTEXITCODE -ne 0) {
            throw "Git ref must be an immutable tag or full commit; branches and abbreviated commits are not allowed: $Ref"
        }
    }
    & git -C $checkout checkout --quiet --detach $resolvedCommit.Trim()
    Assert-LastExitCode "Git checkout"

    $env:PYTHONPATH = Join-Path $checkout "src"
    # Hand the resolved checkout to Core instead of letting it clone again. A
    # second clone would re-resolve the tag, so a tag that moved in between
    # would install a different commit than the one verified above.
    $installArgs = @(
        "-m", "isekai", "install",
        "--source", $Source,
        "--ref", $Ref,
        "--path", $resolvedProject,
        "--checkout", $checkout
    )
    foreach ($selectedRuntime in $Runtime) {
        $installArgs += @("--runtime", $selectedRuntime)
    }
    if ($AdoptFoundation) {
        $installArgs += "--adopt-foundation"
    }

    & $pythonExecutable @pythonPrefix @installArgs
    Assert-LastExitCode "ISEKAI install"

    if ($Init) {
        $projectManifest = Join-Path $resolvedProject "project.json"
        if (Test-Path -LiteralPath $projectManifest) {
            Write-Warning "ISEKAI project already initialized: $projectManifest"
        }
        else {
            $launcher = Join-Path $resolvedProject ".isekai/bin/isekai.py"
            $initArgs = @($launcher, "init", "--path", $resolvedProject)
            foreach ($selectedProfile in $Profile) {
                $initArgs += @("--profile", $selectedProfile)
            }
            & $pythonExecutable @pythonPrefix @initArgs
            Assert-LastExitCode "ISEKAI project initialization"
        }
    }

    Write-Host "ISEKAI installation complete: $resolvedProject"
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}

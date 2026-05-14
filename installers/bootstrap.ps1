param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InstallerArgs
)

$ErrorActionPreference = "Stop"

$RepoOwner = "joaotolovi"
$RepoName = "Tiller"
$RepoRef = "master"
$ArchiveUrl = "https://codeload.github.com/$RepoOwner/$RepoName/zip/refs/heads/$RepoRef"

function Write-Info($Message) { Write-Host "[tiller-bootstrap] $Message" -ForegroundColor Cyan }
function Invoke-Die($Message) { Write-Host "[tiller-bootstrap] ERROR: $Message" -ForegroundColor Red; exit 1 }

$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("tiller-bootstrap-" + [guid]::NewGuid())
$zipPath = Join-Path $tmpDir "tiller.zip"
$extractPath = Join-Path $tmpDir "extract"

New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
New-Item -ItemType Directory -Force -Path $extractPath | Out-Null

try {
    Write-Info "Downloading $RepoOwner/$RepoName@$RepoRef"
    Invoke-WebRequest -Uri $ArchiveUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

    $sourceDir = Join-Path $extractPath "$RepoName-$RepoRef"
    if (-not (Test-Path $sourceDir)) {
        Invoke-Die "Extracted installer directory not found"
    }

    $installerPath = Join-Path $sourceDir "installers\install.ps1"
    if (-not (Test-Path $installerPath)) {
        Invoke-Die "Installer script not found inside downloaded package"
    }

    & powershell -ExecutionPolicy Bypass -File $installerPath @InstallerArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}

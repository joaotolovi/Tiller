# =============================================================================
# Tiller Installer — Windows (PowerShell 5.1+)
# =============================================================================
param(
    [ValidateSet("install", "upgrade", "reinstall", "uninstall")]
    [string]$Mode        = "install",
    [string]$InstallDir  = "",
    [string]$ConfigPath  = "",
    [string]$ServiceName = "Tiller",
    [string]$LogDir      = "",
    [string]$RepoOwner   = "joaotolovi",
    [string]$RepoName    = "Tiller",
    [string]$RepoRef     = "master"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Tiller"
}
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $env:APPDATA "Tiller\tiller.yaml"
}
if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $LogDir = Join-Path $env:LOCALAPPDATA "Tiller\logs"
}

$ConfigDir = Split-Path -Parent $ConfigPath
$ArchiveUrl = "https://codeload.github.com/$RepoOwner/$RepoName/zip/refs/heads/$RepoRef"
$GhVersion = if ($env:TILLER_GH_VERSION) { $env:TILLER_GH_VERSION } else { "2.92.0" }
$TillerBinDir = if ($env:TILLER_BIN_DIR) { $env:TILLER_BIN_DIR } else { Join-Path $InstallDir "bin" }

function Write-Info($Message)    { Write-Host "[tiller-install] $Message" -ForegroundColor Cyan }
function Write-Success($Message) { Write-Host "[tiller-install] $Message" -ForegroundColor Green }
function Write-Warn($Message)    { Write-Host "[tiller-install] WARNING: $Message" -ForegroundColor Yellow }
function Invoke-Die($Message)    { Write-Host "[tiller-install] ERROR: $Message" -ForegroundColor Red; exit 1 }

function Ensure-ExecutionPolicy {
    $scope  = "CurrentUser"
    $policy = Get-ExecutionPolicy -Scope $scope
    if ($policy -in @("Restricted", "AllSigned")) {
        Write-Info "Setting ExecutionPolicy to RemoteSigned for CurrentUser..."
        try {
            Set-ExecutionPolicy -Scope $scope -ExecutionPolicy RemoteSigned -Force
        } catch {
            Write-Warn "Could not set ExecutionPolicy automatically."
        }
    }
}

function Get-UvPath {
    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found) {
        Write-Info "Found uv at $($found.Source)"
        return $found.Source
    }

    $candidates = @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $HOME ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uv\bin\uv.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $env:PATH = "$(Split-Path $c);$env:PATH"
            Write-Info "Found uv at $c"
            return $c
        }
    }

    Write-Info "uv not found — installing..."
    try {
        powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    } catch {
        Invoke-Die "Failed to install uv: $_"
    }

    foreach ($p in @((Join-Path $HOME ".local\bin"), (Join-Path $HOME ".cargo\bin"), (Join-Path $env:LOCALAPPDATA "uv\bin"))) {
        if ($env:PATH -notlike "*$p*") {
            $env:PATH = "$p;$env:PATH"
        }
    }

    foreach ($c in $candidates) {
        if (Test-Path $c) {
            Write-Success "uv installed at $c"
            return $c
        }
    }

    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }

    Invoke-Die "uv installation finished but binary was not found."
}

function Get-GhPath {
    $found = Get-Command gh -ErrorAction SilentlyContinue
    if ($found) {
        Write-Info "Found gh at $($found.Source)"
        return $found.Source
    }

    $bundled = Join-Path $TillerBinDir "gh.exe"
    if (Test-Path $bundled) {
        $env:PATH = "$TillerBinDir;$env:PATH"
        Write-Info "Found gh at $bundled"
        return $bundled
    }

    $arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "386" }
    $archiveName = "gh_${GhVersion}_windows_${arch}.zip"
    $downloadUrl = "https://github.com/cli/cli/releases/download/v$GhVersion/$archiveName"
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("tiller-gh-" + [guid]::NewGuid())
    $zipPath = Join-Path $tmpDir $archiveName
    $extractPath = Join-Path $tmpDir "extract"
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    New-Item -ItemType Directory -Force -Path $extractPath | Out-Null
    New-Item -ItemType Directory -Force -Path $TillerBinDir | Out-Null

    try {
        Write-Info "Installing GitHub CLI locally..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
        $candidate = Join-Path $extractPath "gh_${GhVersion}_windows_${arch}\bin\gh.exe"
        if (-not (Test-Path $candidate)) {
            Write-Warn "Downloaded GitHub CLI archive did not contain expected gh.exe binary"
            return $null
        }
        Copy-Item -Path $candidate -Destination $bundled -Force
        $env:PATH = "$TillerBinDir;$env:PATH"
        Write-Success "Installed gh at $bundled"
        return $bundled
    } catch {
        Write-Warn "Could not install GitHub CLI automatically: $_"
        return $null
    } finally {
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    }
}

function Test-IsInstalled {
    return (Test-Path (Join-Path $InstallDir "pyproject.toml"))
}

function Download-Source {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("tiller-install-" + [guid]::NewGuid())
    $zipPath = Join-Path $tmpDir "tiller.zip"
    $extractPath = Join-Path $tmpDir "extract"

    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    New-Item -ItemType Directory -Force -Path $extractPath | Out-Null

    try {
        Write-Info "Downloading Tiller source from $RepoOwner/$RepoName@$RepoRef"
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $zipPath
        Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

        $sourceDir = Join-Path $extractPath "$RepoName-$RepoRef"
        if (-not (Test-Path $sourceDir)) {
            Invoke-Die "Unable to locate extracted source directory"
        }

        if (Test-Path $InstallDir) {
            Get-ChildItem -Force $InstallDir | Remove-Item -Recurse -Force
        }
        Copy-Item -Path (Join-Path $sourceDir "*") -Destination $InstallDir -Recurse -Force
        Write-Success "Installed source into $InstallDir"
    } finally {
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    }
}

function Sync-Runtime($UvPath) {
    Write-Info "Syncing project dependencies..."
    Push-Location $InstallDir
    try {
        & $UvPath sync -q --no-progress | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Invoke-Die "Failed to sync dependencies."
        }
    } finally {
        Pop-Location
    }
}

function Run-Setup($UvPath, $GhPath) {
    if (Test-Path $ConfigPath) {
        Write-Info "Config already exists at $ConfigPath — skipping interactive setup."
        return
    }

    if (-not $Host.UI.RawUI) {
        Write-Warn "No interactive terminal detected — skipping setup."
        Write-Warn "Provide your config manually at: $ConfigPath"
        return
    }

    Write-Info "Running interactive setup..."
    Push-Location $InstallDir
    try {
        if ($GhPath) {
            $env:TILLER_GH_PATH = $GhPath
        }
        & $UvPath run tiller setup --config $ConfigPath | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Invoke-Die "Setup failed."
        }
    } finally {
        Pop-Location
    }
}

function Write-Runner($UvPath) {
    $RunnerPath = Join-Path $InstallDir ".tiller-run.ps1"
    @"
`$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path '$LogDir' | Out-Null
Set-Location '$InstallDir'
& '$UvPath' run tiller run --config '$ConfigPath' *>> '$LogDir\tiller.log'
"@ | Set-Content -Path $RunnerPath -Encoding UTF8
    Write-Info "Runner script written to $RunnerPath"
    return $RunnerPath
}

function Install-ScheduledTask($RunnerPath) {
    $TaskName = $ServiceName
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$RunnerPath`""
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

    try {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    } catch {
    }

    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    } catch {
    }

    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Tiller background service" | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Success "Installed scheduled task: $TaskName"
}

function Uninstall-ScheduledTask {
    try {
        Unregister-ScheduledTask -TaskName $ServiceName -Confirm:$false -ErrorAction SilentlyContinue
    } catch {
    }
    Write-Info "Removed scheduled task: $ServiceName"
}

function Handle-Mode {
    switch ($Mode) {
        "install" {
            if (Test-IsInstalled) {
                if ($Host.UI.RawUI) {
                    Write-Host ""
                    Write-Info "Tiller is already installed at $InstallDir"
                    Write-Host "[tiller-install] Choose what to do:"
                    Write-Host "  1) upgrade    - update existing installation in place"
                    Write-Host "  2) reinstall  - replace installation in place"
                    Write-Host "  3) uninstall  - remove installed app and service"
                    Write-Host "  4) cancel     - exit without changes"
                    $choice = Read-Host ">"
                    switch ($choice.ToLowerInvariant()) {
                        "1" { $Mode = "upgrade" }
                        "upgrade" { $Mode = "upgrade" }
                        "2" { $Mode = "reinstall" }
                        "reinstall" { $Mode = "reinstall" }
                        "3" { $Mode = "uninstall" }
                        "uninstall" { $Mode = "uninstall" }
                        "4" { Invoke-Die "Installation cancelled. Re-run with -Mode upgrade, reinstall, or uninstall if you want a non-interactive mode." }
                        "cancel" { Invoke-Die "Installation cancelled. Re-run with -Mode upgrade, reinstall, or uninstall if you want a non-interactive mode." }
                        default { Invoke-Die "Invalid choice. Re-run and choose upgrade, reinstall, uninstall, or cancel." }
                    }
                    Handle-Mode
                    return
                }
                Invoke-Die "Tiller is already installed at $InstallDir. Available modes: upgrade, reinstall, uninstall. Re-run with -Mode <mode> in non-interactive environments."
            }
        }
        "upgrade" {
            if (-not (Test-IsInstalled)) {
                Invoke-Die "No existing Tiller installation found at $InstallDir. Re-run with -Mode install."
            }
            Write-Info "Existing installation detected — upgrading in place."
        }
        "reinstall" {
            if (Test-IsInstalled) {
                Write-Info "Existing installation detected — reinstalling in place."
            } else {
                Write-Warn "No existing installation found — proceeding with fresh install."
            }
        }
        "uninstall" {
            Uninstall-ScheduledTask
            if (Test-Path $InstallDir) {
                Remove-Item -Recurse -Force $InstallDir
            }
            Write-Success "Tiller uninstalled"
            Write-Info "Install dir removed: $InstallDir"
            Write-Info "Config preserved at: $ConfigPath"
            Write-Info "Logs preserved at: $LogDir"
            exit 0
        }
    }
}

Ensure-ExecutionPolicy
Handle-Mode
$UvPath = Get-UvPath
Download-Source
Sync-Runtime -UvPath $UvPath
$GhPath = Get-GhPath
Run-Setup -UvPath $UvPath -GhPath $GhPath
$RunnerPath = Write-Runner -UvPath $UvPath
Install-ScheduledTask -RunnerPath $RunnerPath
Write-Success "Tiller installation completed"
Write-Info "Mode: $Mode"
Write-Info "Install dir: $InstallDir"
Write-Info "Config: $ConfigPath"
Write-Info "Logs: $LogDir"

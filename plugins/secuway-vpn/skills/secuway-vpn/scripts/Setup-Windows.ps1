[CmdletBinding()]
param(
    [ValidateSet("Install", "Uninstall", "Status")]
    [string]$Action = "Install",

    [string]$TargetSid,

    [string]$TargetBin,

    [string]$OpenVPNMsiPath,

    [switch]$Elevated
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$skillRoot = Split-Path -Parent $PSScriptRoot
$assetRoot = Join-Path $skillRoot "assets"
$manifestPath = Join-Path $assetRoot "manifest.json"
$runtimeInstaller = Join-Path $PSScriptRoot "Install-WindowsRuntime.ps1"
$cliStateName = ".secuway-cli-install.json"

function Test-IsWindows {
    return [Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [Runtime.InteropServices.OSPlatform]::Windows
    )
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Get-NativeArchitecture {
    $architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture
    switch ($architecture.ToString().ToUpperInvariant()) {
        "X64" { return "amd64" }
        "ARM64" { return "arm64" }
        default { throw "Unsupported Windows architecture: $architecture" }
    }
}

function Get-AssetManifest {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Bundled asset manifest is missing"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema -ne "secuway-windows-assets/v1") {
        throw "Unknown bundled asset manifest schema"
    }
    return $manifest
}

function Get-AssetRecord {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Manifest,

        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $property = $Manifest.assets.PSObject.Properties[$RelativePath]
    if (-not $property) {
        throw "Asset is not recorded in the manifest: $RelativePath"
    }
    return $property.Value
}

function Assert-Asset {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Manifest,

        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $path = Join-Path $assetRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Bundled asset is missing: $RelativePath"
    }
    $record = Get-AssetRecord -Manifest $Manifest -RelativePath $RelativePath
    $item = Get-Item -LiteralPath $path
    if ($item.Length -ne [long]$record.bytes) {
        throw "Bundled asset size mismatch: $RelativePath"
    }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne [string]$record.sha256) {
        throw "Bundled asset hash mismatch: $RelativePath"
    }
    return $path
}

function ConvertTo-PowerShellLiteral {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )
    return "'" + $Value.Replace("'", "''") + "'"
}

function Invoke-Elevated {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RequestedAction,

        [Parameter(Mandatory = $true)]
        [string]$RequestedSid,

        [Parameter(Mandatory = $true)]
        [string]$RequestedBin,

        [string]$RequestedMsi
    )

    if (-not $PSCommandPath) {
        throw "Elevation requires running this file as a script"
    }
    $hostExecutable = (Get-Process -Id $PID).Path
    $parts = @(
        "& $(ConvertTo-PowerShellLiteral -Value $PSCommandPath)",
        "-Action $(ConvertTo-PowerShellLiteral -Value $RequestedAction)",
        "-TargetSid $(ConvertTo-PowerShellLiteral -Value $RequestedSid)",
        "-TargetBin $(ConvertTo-PowerShellLiteral -Value $RequestedBin)",
        "-Elevated"
    )
    if ($RequestedMsi) {
        $parts += "-OpenVPNMsiPath $(ConvertTo-PowerShellLiteral -Value $RequestedMsi)"
    }
    $command = $parts -join " "
    $encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($command)
    )
    Write-Host "INFO Requesting one-time administrator approval"
    $process = Start-Process `
        -FilePath $hostExecutable `
        -Verb RunAs `
        -ArgumentList @(
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            $encoded
        ) `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Elevated Windows setup failed with code $($process.ExitCode)"
    }
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $temporary = "$Path.new-$([Guid]::NewGuid().ToString('N'))"
    try {
        $json = $Value | ConvertTo-Json -Depth 5
        [IO.File]::WriteAllText(
            $temporary,
            $json,
            [Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Get-ExistingRuntimeState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Sid
    )

    $registryPath = "HKLM:\SOFTWARE\OpenVPN"
    if (-not (Test-Path $registryPath)) {
        return $null
    }
    $key = Get-Item -Path $registryPath
    $installDirectory = [string]$key.GetValue("")
    if (-not $installDirectory) {
        throw "OpenVPN registry is missing its install directory"
    }
    $statePath = Join-Path (
        Join-Path ([IO.Path]::GetFullPath($installDirectory)) "ssl\modules"
    ) ".mrcha-secuway-install.json"
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return $null
    }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.schema -ne "secuway-windows-install/v1" -or
        $state.target_sid -ne $Sid) {
        throw "Existing Secuway runtime state does not match this user"
    }
    return $state
}

function Install-UserCli {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$DestinationDirectory,

        [Parameter(Mandatory = $true)]
        [string]$Sid
    )

    [IO.Directory]::CreateDirectory($DestinationDirectory) | Out-Null
    $destination = Join-Path $DestinationDirectory "secuway.exe"
    $statePath = Join-Path $DestinationDirectory $cliStateName
    $existingState = $null
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        $existingState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($existingState.schema -ne "secuway-windows-cli-install/v1" -or
            $existingState.target_sid -ne $Sid) {
            throw "Existing Secuway CLI installation state is not owned by this user"
        }
    }
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        $existingHash = (
            Get-FileHash -LiteralPath $destination -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if (-not $existingState -or
            $existingHash -ne [string]$existingState.installed_sha256) {
            throw "Existing secuway.exe is unmanaged or changed; refusing to overwrite it"
        }
    }

    $backup = $null
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        $backup = "$destination.backup-$([Guid]::NewGuid().ToString('N'))"
        Copy-Item -LiteralPath $destination -Destination $backup
    }
    $oldPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathForEntries = if ($null -eq $oldPath) { "" } else { $oldPath }
    $entries = @(
        $pathForEntries.Split(";") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -ne "" }
    )
    $alreadyPresent = @(
        $entries |
            Where-Object {
                $_.TrimEnd("\").Equals(
                    $DestinationDirectory.TrimEnd("\"),
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
    ).Count -gt 0
    $addedPath = -not $alreadyPresent
    $newPath = $oldPath

    try {
        $temporary = "$destination.new-$([Guid]::NewGuid().ToString('N'))"
        try {
            Copy-Item -LiteralPath $Source -Destination $temporary
            $sourceHash = (
                Get-FileHash -LiteralPath $Source -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            $copiedHash = (
                Get-FileHash -LiteralPath $temporary -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            if ($copiedHash -ne $sourceHash) {
                throw "Secuway CLI copy hash mismatch"
            }
            Move-Item -LiteralPath $temporary -Destination $destination -Force
        }
        finally {
            if (Test-Path -LiteralPath $temporary) {
                Remove-Item -LiteralPath $temporary -Force
            }
        }
        if ($addedPath) {
            if ([string]::IsNullOrEmpty($oldPath)) {
                $newPath = $DestinationDirectory
            }
            elseif ($oldPath.EndsWith(";")) {
                $newPath = "$oldPath$DestinationDirectory"
            }
            else {
                $newPath = "$oldPath;$DestinationDirectory"
            }
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        }
        return [pscustomobject]@{
            Destination = $destination
            StatePath = $statePath
            ExistingState = $existingState
            Backup = $backup
            OldPath = $oldPath
            NewPath = $newPath
            AddedPath = $addedPath
            InstalledHash = $sourceHash
        }
    }
    catch {
        if ($backup -and (Test-Path -LiteralPath $backup)) {
            Move-Item -LiteralPath $backup -Destination $destination -Force
        }
        elseif (Test-Path -LiteralPath $destination) {
            Remove-Item -LiteralPath $destination -Force
        }
        [Environment]::SetEnvironmentVariable("Path", $oldPath, "User")
        throw
    }
}

function Undo-UserCliInstall {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Change
    )

    if ($Change.ExistingState) {
        Write-AtomicJson -Path $Change.StatePath -Value $Change.ExistingState
    }
    elseif (Test-Path -LiteralPath $Change.StatePath) {
        Remove-Item -LiteralPath $Change.StatePath -Force
    }
    if ($Change.Backup -and (Test-Path -LiteralPath $Change.Backup)) {
        Move-Item `
            -LiteralPath $Change.Backup `
            -Destination $Change.Destination `
            -Force
    }
    elseif (Test-Path -LiteralPath $Change.Destination) {
        Remove-Item -LiteralPath $Change.Destination -Force
    }
    [Environment]::SetEnvironmentVariable("Path", $Change.OldPath, "User")
}

function Complete-UserCliInstall {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Change,

        [Parameter(Mandatory = $true)]
        [string]$Sid,

        [Parameter(Mandatory = $true)]
        [string]$Architecture
    )

    $pathAddedForState = $Change.AddedPath
    $pathBeforeInstall = $null
    $pathAfterInstall = $null
    if ($Change.AddedPath) {
        $pathBeforeInstall = $Change.OldPath
        $pathAfterInstall = $Change.NewPath
    }
    if ($Change.ExistingState) {
        $pathAddedForState = (
            [bool]$Change.ExistingState.path_added -or
            [bool]$Change.AddedPath
        )
        if ([bool]$Change.ExistingState.path_added -and
            -not [bool]$Change.AddedPath) {
            $beforeProperty = (
                $Change.ExistingState.PSObject.Properties[
                    "path_before_install"
                ]
            )
            $afterProperty = (
                $Change.ExistingState.PSObject.Properties[
                    "path_after_install"
                ]
            )
            if (-not $beforeProperty -or -not $afterProperty) {
                throw "Existing CLI state cannot safely restore the user PATH"
            }
            $pathBeforeInstall = $beforeProperty.Value
            $pathAfterInstall = $afterProperty.Value
        }
    }
    $state = [ordered]@{
        schema = "secuway-windows-cli-install/v1"
        version = "0.4.0"
        architecture = $Architecture
        target_sid = $Sid
        cli_path = $Change.Destination
        installed_sha256 = $Change.InstalledHash
        path_added = $pathAddedForState
        path_before_install = $pathBeforeInstall
        path_after_install = $pathAfterInstall
        secrets_printed = $false
    }
    Write-AtomicJson -Path $Change.StatePath -Value $state
    if ($Change.Backup -and (Test-Path -LiteralPath $Change.Backup)) {
        Remove-Item -LiteralPath $Change.Backup -Force
    }
}

function Get-VerifiedOpenVPNMsi {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Manifest,

        [Parameter(Mandatory = $true)]
        [string]$Architecture,

        [string]$ExistingPath
    )

    $record = $Manifest.openvpn.$Architecture
    $path = $ExistingPath
    $downloaded = $false
    if (-not $path) {
        $path = Join-Path (
            [IO.Path]::GetTempPath()
        ) "OpenVPN-$($Manifest.openvpn.version)-$Architecture.msi"
        Invoke-WebRequest -Uri ([string]$record.url) -OutFile $path
        $downloaded = $true
    }
    $path = [IO.Path]::GetFullPath($path)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Official OpenVPN MSI is missing"
    }
    $actual = (
        Get-FileHash -LiteralPath $path -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($actual -ne [string]$record.sha256) {
        throw "Official OpenVPN MSI hash mismatch"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or
        -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch "OpenVPN") {
        throw "Official OpenVPN MSI signature is not valid"
    }
    return [pscustomobject]@{ Path = $path; Downloaded = $downloaded }
}

function Invoke-ElevatedInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Sid,

        [Parameter(Mandatory = $true)]
        [string]$Provider,

        [string]$MsiPath
    )

    if (-not (Test-IsAdministrator)) {
        throw "Elevated setup did not receive administrator rights"
    }
    if (-not (Test-Path "HKLM:\SOFTWARE\OpenVPN")) {
        if (-not $MsiPath) {
            throw "Official OpenVPN installation is required"
        }
        $process = Start-Process `
            -FilePath "msiexec.exe" `
            -ArgumentList @("/i", "`"$MsiPath`"", "/qn", "/norestart") `
            -Wait `
            -PassThru
        if ($process.ExitCode -notin @(0, 3010)) {
            throw "Official OpenVPN installation failed with code $($process.ExitCode)"
        }
    }
    & $runtimeInstaller `
        -Action Install `
        -LeaProviderPath $Provider `
        -TargetSid $Sid `
        -Elevated
    if ($LASTEXITCODE -ne 0) {
        throw "Secuway Windows runtime installation failed"
    }
}

if (-not (Test-IsWindows)) {
    throw "This setup script must run on Windows"
}
if (-not [Environment]::Is64BitProcess) {
    throw "Use 64-bit PowerShell"
}
if (-not $TargetSid) {
    $TargetSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}
try {
    [void][Security.Principal.SecurityIdentifier]::new($TargetSid)
}
catch {
    throw "TargetSid is not a valid Windows SID"
}
if (-not $Elevated) {
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if (-not $TargetSid.Equals(
            $currentSid,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "TargetSid must identify the current user"
    }
}
if (-not $TargetBin) {
    $TargetBin = Join-Path $env:LOCALAPPDATA "mrcha-skills\secuway\bin"
}
$TargetBin = [IO.Path]::GetFullPath($TargetBin)

$architecture = Get-NativeArchitecture
$manifest = Get-AssetManifest
$relativeRoot = "windows-$architecture"
$cliAsset = Assert-Asset `
    -Manifest $manifest `
    -RelativePath "$relativeRoot/secuway.exe"
$providerAsset = Assert-Asset `
    -Manifest $manifest `
    -RelativePath "$relativeRoot/lea.dll"
[void](Assert-Asset `
    -Manifest $manifest `
    -RelativePath "$relativeRoot/provider_smoke.exe")

if ($Action -eq "Status") {
    & $runtimeInstaller -Action Status -TargetSid $TargetSid
    exit $LASTEXITCODE
}

if ($Elevated) {
    if ($Action -eq "Install") {
        $verifiedMsi = $null
        if (-not (Test-Path "HKLM:\SOFTWARE\OpenVPN")) {
            $verifiedMsi = Get-VerifiedOpenVPNMsi `
                -Manifest $manifest `
                -Architecture $architecture `
                -ExistingPath $OpenVPNMsiPath
        }
        Invoke-ElevatedInstall `
            -Sid $TargetSid `
            -Provider $providerAsset `
            -MsiPath $(if ($verifiedMsi) { $verifiedMsi.Path } else { $null })
        exit 0
    }
    & $runtimeInstaller `
        -Action Uninstall `
        -TargetSid $TargetSid `
        -Elevated
    exit $LASTEXITCODE
}

if ($Action -eq "Install") {
    $runtimeStateBefore = Get-ExistingRuntimeState -Sid $TargetSid
    $runtimeInstalledThisRun = $false
    $change = Install-UserCli `
        -Source $cliAsset `
        -DestinationDirectory $TargetBin `
        -Sid $TargetSid
    $verifiedMsi = $null
    try {
        if (-not (Test-Path "HKLM:\SOFTWARE\OpenVPN")) {
            $verifiedMsi = Get-VerifiedOpenVPNMsi `
                -Manifest $manifest `
                -Architecture $architecture `
                -ExistingPath $OpenVPNMsiPath
        }
        if (Test-IsAdministrator) {
            Invoke-ElevatedInstall `
                -Sid $TargetSid `
                -Provider $providerAsset `
                -MsiPath $(if ($verifiedMsi) { $verifiedMsi.Path } else { $null })
        }
        else {
            Invoke-Elevated `
                -RequestedAction Install `
                -RequestedSid $TargetSid `
                -RequestedBin $TargetBin `
                -RequestedMsi $(if ($verifiedMsi) { $verifiedMsi.Path } else { $null })
        }
        $runtimeInstalledThisRun = $null -eq $runtimeStateBefore
        & $change.Destination doctor
        if ($LASTEXITCODE -ne 0) {
            throw "Installed Secuway CLI doctor failed"
        }
        Complete-UserCliInstall `
            -Change $change `
            -Sid $TargetSid `
            -Architecture $architecture
        Write-Host "READY Secuway VPN Windows support installed"
        Write-Host "INFO CLI path: $($change.Destination)"
        Write-Host "INFO Open a new terminal to use secuway from PATH"
    }
    catch {
        $installError = $_
        $rollbackFailures = [System.Collections.Generic.List[string]]::new()
        if ($runtimeInstalledThisRun) {
            try {
                if (Test-IsAdministrator) {
                    & $runtimeInstaller `
                        -Action Uninstall `
                        -TargetSid $TargetSid `
                        -Elevated
                    if ($LASTEXITCODE -ne 0) {
                        throw "runtime uninstall returned $LASTEXITCODE"
                    }
                }
                else {
                    Invoke-Elevated `
                        -RequestedAction Uninstall `
                        -RequestedSid $TargetSid `
                        -RequestedBin $TargetBin
                }
            }
            catch {
                $rollbackFailures.Add(
                    "Windows runtime rollback failed: $($_.Exception.Message)"
                )
            }
        }
        try {
            Undo-UserCliInstall -Change $change
        }
        catch {
            $rollbackFailures.Add(
                "user CLI rollback failed: $($_.Exception.Message)"
            )
        }
        if ($rollbackFailures.Count -gt 0) {
            throw (
                "$($installError.Exception.Message); " +
                ($rollbackFailures -join "; ")
            )
        }
        throw $installError
    }
    finally {
        if ($verifiedMsi -and $verifiedMsi.Downloaded -and
            (Test-Path -LiteralPath $verifiedMsi.Path)) {
            try {
                Remove-Item -LiteralPath $verifiedMsi.Path -Force
            }
            catch {
                Write-Warning "Could not remove the verified OpenVPN MSI download"
            }
        }
    }
    exit 0
}

$statePath = Join-Path $TargetBin $cliStateName
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    throw "Secuway CLI installation state is missing; nothing was removed"
}
$cliState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($cliState.schema -ne "secuway-windows-cli-install/v1" -or
    $cliState.target_sid -ne $TargetSid) {
    throw "Secuway CLI installation state does not match this user"
}
$cliPath = [IO.Path]::GetFullPath([string]$cliState.cli_path)
$expectedCliPath = Join-Path $TargetBin "secuway.exe"
if (-not $cliPath.Equals(
        [IO.Path]::GetFullPath($expectedCliPath),
        [StringComparison]::OrdinalIgnoreCase
    ) -or
    -not (Test-Path -LiteralPath $cliPath -PathType Leaf) -or
    (Get-FileHash -LiteralPath $cliPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne
        [string]$cliState.installed_sha256) {
    throw "Installed secuway.exe is missing or changed; refusing to remove it"
}
$restoreUserPath = $false
$pathBeforeInstall = $null
if ([bool]$cliState.path_added) {
    $beforeProperty = $cliState.PSObject.Properties["path_before_install"]
    $afterProperty = $cliState.PSObject.Properties["path_after_install"]
    if (-not $beforeProperty -or -not $afterProperty -or
        $null -eq $afterProperty.Value) {
        throw "CLI state cannot safely restore the user PATH"
    }
    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not [object]::Equals($currentUserPath, $afterProperty.Value)) {
        throw "User PATH changed since installation; refusing to overwrite it"
    }
    $pathBeforeInstall = $beforeProperty.Value
    $restoreUserPath = $true
}
if (Test-IsAdministrator) {
    & $runtimeInstaller `
        -Action Uninstall `
        -TargetSid $TargetSid `
        -Elevated
    if ($LASTEXITCODE -ne 0) {
        throw "Secuway Windows runtime uninstall failed"
    }
}
else {
    Invoke-Elevated `
        -RequestedAction Uninstall `
        -RequestedSid $TargetSid `
        -RequestedBin $TargetBin
}
Remove-Item -LiteralPath $cliPath -Force
Remove-Item -LiteralPath $statePath -Force
if ($restoreUserPath) {
    [Environment]::SetEnvironmentVariable(
        "Path",
        $pathBeforeInstall,
        "User"
    )
}
if ((Test-Path -LiteralPath $TargetBin -PathType Container) -and
    @(Get-ChildItem -LiteralPath $TargetBin -Force).Count -eq 0) {
    Remove-Item -LiteralPath $TargetBin -Force
}
Write-Host "PASS Secuway VPN Windows support removed"
Write-Host "INFO Official OpenVPN was left installed"

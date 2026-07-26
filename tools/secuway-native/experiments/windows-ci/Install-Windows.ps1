[CmdletBinding()]
param(
    [ValidateSet("Install", "Uninstall", "Status")]
    [string]$Action = "Install",

    [string]$LeaProviderPath,

    [string]$TargetSid,

    [switch]$Elevated
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$stateFileName = ".mrcha-secuway-install.json"
$providerFileName = "lea.dll"
$backupFileName = "lea.dll.pre-mrcha-secuway"
$serviceName = "OpenVPNServiceInteractive"

function Test-IsWindows {
    return [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-NativeArchitecture {
    $architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    switch ($architecture.ToUpperInvariant()) {
        "X64" { return "amd64" }
        "ARM64" { return "arm64" }
        default { throw "Unsupported Windows architecture: $architecture" }
    }
}

function Get-PEInformation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $stream = [System.IO.File]::OpenRead((Resolve-Path -LiteralPath $Path))
    $reader = [System.IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) {
            throw "Not a PE file: $Path"
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadInt32()
        if ($peOffset -lt 0x40 -or $peOffset -gt ($stream.Length - 24)) {
            throw "Invalid PE header offset: $Path"
        }
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) {
            throw "Missing PE signature: $Path"
        }
        $machine = $reader.ReadUInt16()
        $architecture = switch ($machine) {
            0x8664 { "amd64" }
            0xAA64 { "arm64" }
            default { "unknown-0x{0:X4}" -f $machine }
        }
        $stream.Position = $peOffset + 22
        $characteristics = $reader.ReadUInt16()
        return [pscustomobject]@{
            Architecture = $architecture
            IsDll = (($characteristics -band 0x2000) -ne 0)
        }
    }
    finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing a reparse-point install target: $Path"
    }
}

function Assert-OfficialOpenVPNSignature {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "$Label does not have a valid Authenticode signature"
    }
    if (-not $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch "OpenVPN") {
        throw "$Label signer is not recognized as OpenVPN"
    }
}

function Get-OpenVPNSetting {
    $registryPath = "HKLM:\SOFTWARE\OpenVPN"
    if (-not (Test-Path $registryPath)) {
        throw "Official OpenVPN installation is required before running this installer"
    }
    $key = Get-Item -Path $registryPath
    $installDirectory = [string]$key.GetValue("")
    if (-not $installDirectory) {
        throw "OpenVPN registry is missing its install directory"
    }
    $installDirectory = [IO.Path]::GetFullPath($installDirectory)
    if (-not (Test-Path -LiteralPath $installDirectory -PathType Container)) {
        throw "OpenVPN install directory from the registry does not exist"
    }

    $configDirectory = [string]$key.GetValue("config_dir")
    if (-not $configDirectory) {
        $configDirectory = Join-Path $installDirectory "config"
    }
    $configDirectory = [IO.Path]::GetFullPath($configDirectory)
    $configRoot = [IO.Path]::GetPathRoot($configDirectory).TrimEnd("\")
    if ($configDirectory.TrimEnd("\") -eq $configRoot) {
        throw "OpenVPN config_dir cannot be a drive root"
    }

    $openVPNExecutable = [string]$key.GetValue("exe_path")
    if (-not $openVPNExecutable) {
        $openVPNExecutable = Join-Path $installDirectory "bin\openvpn.exe"
    }
    $openVPNExecutable = [IO.Path]::GetFullPath($openVPNExecutable)
    if (-not (Test-Path -LiteralPath $openVPNExecutable -PathType Leaf)) {
        throw "OpenVPN executable from the registry does not exist"
    }
    $installPrefix = $installDirectory.TrimEnd("\") + "\"
    if (-not $openVPNExecutable.StartsWith($installPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "OpenVPN exe_path is outside the registered install directory"
    }

    Assert-OfficialOpenVPNSignature `
        -Path $openVPNExecutable `
        -Label "OpenVPN executable"

    $openVPNPE = Get-PEInformation -Path $openVPNExecutable
    if ($openVPNPE.IsDll) {
        throw "OpenVPN exe_path points to a DLL"
    }
    $nativeArchitecture = Get-NativeArchitecture
    if ($openVPNPE.Architecture -ne $nativeArchitecture) {
        throw "OpenVPN architecture does not match the native Windows architecture"
    }

    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if (-not $service) {
        throw "Official OpenVPN Interactive Service is not installed"
    }
    $serviceKeyPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName"
    $serviceKey = Get-Item -Path $serviceKeyPath
    $serviceImage = [Environment]::ExpandEnvironmentVariables(
        [string]$serviceKey.GetValue("ImagePath")
    )
    $serviceExecutable = $null
    if ($serviceImage -match '^\s*"([^"]+\.exe)"') {
        $serviceExecutable = $Matches[1]
    }
    elseif ($serviceImage -match '^\s*(.+?\.exe)(?:\s|$)') {
        $serviceExecutable = $Matches[1]
    }
    if (-not $serviceExecutable) {
        throw "OpenVPN Interactive Service ImagePath is invalid"
    }
    $serviceExecutable = [IO.Path]::GetFullPath($serviceExecutable)
    if (-not (Test-Path -LiteralPath $serviceExecutable -PathType Leaf) -or
        -not $serviceExecutable.StartsWith(
            $installPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "OpenVPN Interactive Service executable is outside the registered installation"
    }
    Assert-OfficialOpenVPNSignature `
        -Path $serviceExecutable `
        -Label "OpenVPN Interactive Service executable"
    $servicePE = Get-PEInformation -Path $serviceExecutable
    if ($servicePE.IsDll -or $servicePE.Architecture -ne $nativeArchitecture) {
        throw "OpenVPN Interactive Service architecture does not match Windows"
    }

    return [pscustomobject]@{
        InstallDirectory = $installDirectory
        ConfigDirectory = $configDirectory
        Executable = $openVPNExecutable
        Architecture = $nativeArchitecture
        ModuleDirectory = Join-Path $installDirectory "ssl\modules"
        Service = $service
        ServiceExecutable = $serviceExecutable
    }
}

function ConvertTo-PowerShellLiteral {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )
    return "'" + $Value.Replace("'", "''") + "'"
}

function Invoke-SelfElevated {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RequestedAction,

        [Parameter(Mandatory = $true)]
        [string]$RequestedSid,

        [string]$RequestedProvider
    )

    if (-not $PSCommandPath) {
        throw "Self-elevation requires running this file as a script"
    }
    $hostExecutable = (Get-Process -Id $PID).Path
    $commandParts = @(
        "& $(ConvertTo-PowerShellLiteral -Value $PSCommandPath)",
        "-Action $(ConvertTo-PowerShellLiteral -Value $RequestedAction)",
        "-TargetSid $(ConvertTo-PowerShellLiteral -Value $RequestedSid)",
        "-Elevated"
    )
    if ($RequestedProvider) {
        $commandParts += "-LeaProviderPath $(ConvertTo-PowerShellLiteral -Value $RequestedProvider)"
    }
    $command = $commandParts -join " "
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))

    Write-Host "INFO Requesting one-time administrator approval"
    $process = Start-Process `
        -FilePath $hostExecutable `
        -Verb RunAs `
        -ArgumentList @("-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded) `
        -Wait `
        -PassThru
    exit $process.ExitCode
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
        [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Get-SHA256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Protect-SecuwayProfileDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [Security.Principal.SecurityIdentifier]$UserSid
    )

    $systemSid = [Security.Principal.SecurityIdentifier]::new(
        [Security.Principal.WellKnownSidType]::LocalSystemSid,
        $null
    )
    $administratorsSid = [Security.Principal.SecurityIdentifier]::new(
        [Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid,
        $null
    )
    $inheritance = (
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($administratorsSid)
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $systemSid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        $propagation,
        $allow
    ))
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $administratorsSid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        $propagation,
        $allow
    ))
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $UserSid,
        [Security.AccessControl.FileSystemRights]::Modify,
        $inheritance,
        $propagation,
        $allow
    ))
    Set-Acl -LiteralPath $Path -AclObject $acl

    $verified = Get-Acl -LiteralPath $Path
    if (-not $verified.AreAccessRulesProtected) {
        throw "Profile directory still inherits access rules"
    }
    $rules = $verified.GetAccessRules(
        $true,
        $false,
        [Security.Principal.SecurityIdentifier]
    )
    $requirements = @(
        @{ Sid = $systemSid.Value; Rights = [Security.AccessControl.FileSystemRights]::FullControl },
        @{ Sid = $administratorsSid.Value; Rights = [Security.AccessControl.FileSystemRights]::FullControl },
        @{ Sid = $UserSid.Value; Rights = [Security.AccessControl.FileSystemRights]::Modify }
    )
    foreach ($requirement in $requirements) {
        $matching = @($rules | Where-Object {
            $_.IdentityReference.Value -eq $requirement.Sid -and
            $_.AccessControlType -eq $allow -and
            (($_.FileSystemRights -band $requirement.Rights) -eq $requirement.Rights)
        })
        if ($matching.Count -eq 0) {
            throw "Profile ACL verification failed"
        }
    }
}

function Restore-DirectoryAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Sddl
    )

    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetSecurityDescriptorSddlForm($Sddl)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Test-OpenVPNProvider {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Setting
    )

    $oldModules = $env:OPENSSL_MODULES
    try {
        $env:OPENSSL_MODULES = $Setting.ModuleDirectory
        $ciphers = & $Setting.Executable --providers lea default --show-ciphers 2>&1
        if ($LASTEXITCODE -ne 0 -or ($ciphers -join "`n") -notmatch "LEA-128-CBC") {
            throw "OpenVPN could not load LEA-128-CBC from the installed provider"
        }
        $version = & $Setting.Executable --version 2>&1
        if ($LASTEXITCODE -ne 0 -or ($version -join "`n") -notmatch "\[LZO\]") {
            throw "Official OpenVPN installation does not include LZO support"
        }
    }
    finally {
        $env:OPENSSL_MODULES = $oldModules
    }
}

function Install-SecuwayWindowsSupport {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Setting,

        [Parameter(Mandatory = $true)]
        [string]$ProviderSource,

        [Parameter(Mandatory = $true)]
        [Security.Principal.SecurityIdentifier]$UserSid
    )

    $ProviderSource = [IO.Path]::GetFullPath($ProviderSource)
    if (-not (Test-Path -LiteralPath $ProviderSource -PathType Leaf)) {
        throw "LEA provider source does not exist"
    }
    Assert-NoReparsePoint -Path $ProviderSource
    $sourcePE = Get-PEInformation -Path $ProviderSource
    if (-not $sourcePE.IsDll) {
        throw "LEA provider source is not a Windows DLL"
    }
    if ($sourcePE.Architecture -ne $Setting.Architecture) {
        throw "LEA provider architecture does not match OpenVPN"
    }

    [IO.Directory]::CreateDirectory($Setting.ModuleDirectory) | Out-Null
    [IO.Directory]::CreateDirectory($Setting.ConfigDirectory) | Out-Null
    Assert-NoReparsePoint -Path $Setting.ModuleDirectory
    Assert-NoReparsePoint -Path $Setting.ConfigDirectory

    $providerDestination = Join-Path $Setting.ModuleDirectory $providerFileName
    $backupPath = Join-Path $Setting.ModuleDirectory $backupFileName
    $statePath = Join-Path $Setting.ModuleDirectory $stateFileName
    $baseDirectory = Join-Path $Setting.ConfigDirectory "mrcha-secuway"
    $profileDirectory = Join-Path $baseDirectory $UserSid.Value
    Assert-NoReparsePoint -Path $providerDestination
    Assert-NoReparsePoint -Path $baseDirectory
    Assert-NoReparsePoint -Path $profileDirectory

    $sourceHash = Get-SHA256 -Path $ProviderSource
    $existingState = $null
    if (Test-Path -LiteralPath $statePath) {
        $existingState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($existingState.schema -ne "secuway-windows-install/v1") {
            throw "Unknown Secuway Windows installation state schema"
        }
        if ($existingState.target_sid -ne $UserSid.Value) {
            throw "This host is already provisioned for a different Windows user"
        }
        if ($existingState.installed_sha256 -ne $sourceHash) {
            throw "Installed LEA provider differs from the requested provider"
        }
    }

    $providerChanged = $false
    $backupCreated = $false
    $profileCreatedThisRun = $false
    $baseCreatedThisRun = $false
    $profileCreatedForState = $false
    $baseCreatedForState = $false
    $previousProfileAclForState = $null
    $aclBeforeThisRun = $null
    $serviceStarted = $false
    $providerInstalledByScript = $false
    $backupHash = $null
    $originalServiceWasRunning = $Setting.Service.Status -eq "Running"
    $installedAt = [DateTime]::UtcNow.ToString("o")

    if ($existingState) {
        $profileCreatedForState = [bool]$existingState.profile_directory_created
        $baseCreatedForState = [bool]$existingState.base_directory_created
        $previousProfileAclForState = [string]$existingState.previous_profile_acl_sddl
        $originalServiceWasRunning = [bool]$existingState.service_was_running
        $installedAt = [string]$existingState.installed_at
    }

    try {
        if ($existingState) {
            if (-not (Test-Path -LiteralPath $providerDestination -PathType Leaf) -or
                (Get-SHA256 -Path $providerDestination) -ne $sourceHash) {
                throw "Recorded LEA provider is missing or changed"
            }
            $providerInstalledByScript = [bool]$existingState.provider_installed_by_script
            $backupHash = [string]$existingState.backup_sha256
        }
        else {
            if (Test-Path -LiteralPath $providerDestination -PathType Leaf) {
                $destinationHash = Get-SHA256 -Path $providerDestination
                if ($destinationHash -eq $sourceHash) {
                    $providerInstalledByScript = $false
                }
                else {
                    if (Test-Path -LiteralPath $backupPath) {
                        throw "A previous LEA provider backup already exists"
                    }
                    Move-Item -LiteralPath $providerDestination -Destination $backupPath
                    $backupCreated = $true
                    $backupHash = Get-SHA256 -Path $backupPath
                    $providerInstalledByScript = $true
                }
            }
            else {
                $providerInstalledByScript = $true
            }

            if ($providerInstalledByScript) {
                $copyPath = "$providerDestination.new-$([Guid]::NewGuid().ToString('N'))"
                try {
                    Copy-Item -LiteralPath $ProviderSource -Destination $copyPath
                    if ((Get-SHA256 -Path $copyPath) -ne $sourceHash) {
                        throw "Copied LEA provider hash mismatch"
                    }
                    $copiedPE = Get-PEInformation -Path $copyPath
                    if (-not $copiedPE.IsDll -or
                        $copiedPE.Architecture -ne $Setting.Architecture) {
                        throw "Copied LEA provider architecture mismatch"
                    }
                    Move-Item -LiteralPath $copyPath -Destination $providerDestination -Force
                    $providerChanged = $true
                }
                finally {
                    if (Test-Path -LiteralPath $copyPath) {
                        Remove-Item -LiteralPath $copyPath -Force
                    }
                }
            }
        }

        if (-not (Test-Path -LiteralPath $baseDirectory -PathType Container)) {
            [IO.Directory]::CreateDirectory($baseDirectory) | Out-Null
            $baseCreatedThisRun = $true
            $baseCreatedForState = $true
        }
        if (Test-Path -LiteralPath $profileDirectory -PathType Container) {
            $aclBeforeThisRun = (Get-Acl -LiteralPath $profileDirectory).Sddl
            if (-not $existingState) {
                $previousProfileAclForState = $aclBeforeThisRun
            }
        }
        else {
            [IO.Directory]::CreateDirectory($profileDirectory) | Out-Null
            $profileCreatedThisRun = $true
            $profileCreatedForState = $true
        }
        Protect-SecuwayProfileDirectory -Path $profileDirectory -UserSid $UserSid

        Test-OpenVPNProvider -Setting $Setting

        $state = [ordered]@{
            schema = "secuway-windows-install/v1"
            installed_at = $installedAt
            architecture = $Setting.Architecture
            target_sid = $UserSid.Value
            provider_path = $providerDestination
            installed_sha256 = $sourceHash
            provider_installed_by_script = $providerInstalledByScript
            backup_path = $(if ($backupCreated -or $backupHash) { $backupPath } else { $null })
            backup_sha256 = $backupHash
            profile_directory = $profileDirectory
            profile_directory_created = $profileCreatedForState
            previous_profile_acl_sddl = $previousProfileAclForState
            base_directory = $baseDirectory
            base_directory_created = $baseCreatedForState
            service_was_running = $originalServiceWasRunning
        }
        Write-AtomicJson -Path $statePath -Value $state

        $service = Get-Service -Name $serviceName
        if ($service.Status -ne "Running") {
            Start-Service -Name $serviceName
            $serviceStarted = $true
            $service.WaitForStatus(
                [ServiceProcess.ServiceControllerStatus]::Running,
                [TimeSpan]::FromSeconds(15)
            )
        }

        Write-Host "PASS Official OpenVPN installation and signature"
        Write-Host "PASS Architecture-matched LEA provider and OpenVPN doctor"
        Write-Host "PASS User-isolated profile directory ACL"
        Write-Host "PASS OpenVPN Interactive Service is running"
        Write-Host "READY Windows runtime no longer requires per-connection elevation"
    }
    catch {
        if ($existingState) {
            Write-AtomicJson -Path $statePath -Value $existingState
        }
        elseif (Test-Path -LiteralPath $statePath) {
            Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
        }
        if ($serviceStarted) {
            Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
        }
        if ($profileCreatedThisRun -and (Test-Path -LiteralPath $profileDirectory)) {
            Remove-Item -LiteralPath $profileDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }
        elseif ($aclBeforeThisRun -and (Test-Path -LiteralPath $profileDirectory)) {
            Restore-DirectoryAcl -Path $profileDirectory -Sddl $aclBeforeThisRun
        }
        if ($baseCreatedThisRun -and
            (Test-Path -LiteralPath $baseDirectory) -and
            @(Get-ChildItem -LiteralPath $baseDirectory -Force).Count -eq 0) {
            Remove-Item -LiteralPath $baseDirectory -Force -ErrorAction SilentlyContinue
        }
        if ($providerChanged -and (Test-Path -LiteralPath $providerDestination)) {
            Remove-Item -LiteralPath $providerDestination -Force -ErrorAction SilentlyContinue
        }
        if ($backupCreated -and (Test-Path -LiteralPath $backupPath)) {
            Move-Item -LiteralPath $backupPath -Destination $providerDestination -Force
        }
        throw
    }
}

function Uninstall-SecuwayWindowsSupport {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Setting,

        [Parameter(Mandatory = $true)]
        [Security.Principal.SecurityIdentifier]$UserSid
    )

    $statePath = Join-Path $Setting.ModuleDirectory $stateFileName
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        throw "No Secuway Windows installation state was found; nothing was removed"
    }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.schema -ne "secuway-windows-install/v1" -or
        $state.target_sid -ne $UserSid.Value) {
        throw "Secuway Windows installation state does not match this user"
    }

    $providerDestination = [IO.Path]::GetFullPath([string]$state.provider_path)
    $expectedDestination = Join-Path $Setting.ModuleDirectory $providerFileName
    if (-not $providerDestination.Equals(
            [IO.Path]::GetFullPath($expectedDestination),
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Refusing an unexpected provider removal path"
    }
    $profileDirectory = [IO.Path]::GetFullPath([string]$state.profile_directory)
    $expectedProfile = Join-Path (
        Join-Path $Setting.ConfigDirectory "mrcha-secuway"
    ) $UserSid.Value
    if (-not $profileDirectory.Equals(
            [IO.Path]::GetFullPath($expectedProfile),
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Refusing an unexpected profile removal path"
    }

    if ([bool]$state.provider_installed_by_script) {
        if (-not (Test-Path -LiteralPath $providerDestination -PathType Leaf)) {
            throw "Installed LEA provider is already missing"
        }
        if ((Get-SHA256 -Path $providerDestination) -ne [string]$state.installed_sha256) {
            throw "Installed LEA provider changed; refusing to remove it"
        }
    }

    $removedProvider = $null
    $backupPath = $null
    $backupRestored = $false
    $serviceStopped = $false
    try {
        if (-not [bool]$state.service_was_running) {
            $service = Get-Service -Name $serviceName
            if ($service.Status -eq "Running") {
                Stop-Service -Name $serviceName
                $service.WaitForStatus(
                    [ServiceProcess.ServiceControllerStatus]::Stopped,
                    [TimeSpan]::FromSeconds(15)
                )
                $serviceStopped = $true
            }
        }
        if ([bool]$state.provider_installed_by_script) {
            $removedProvider = "$providerDestination.removing-$([Guid]::NewGuid().ToString('N'))"
            Move-Item -LiteralPath $providerDestination -Destination $removedProvider
            if ($state.backup_path) {
                $backupPath = [IO.Path]::GetFullPath([string]$state.backup_path)
                $expectedBackup = Join-Path $Setting.ModuleDirectory $backupFileName
                if (-not $backupPath.Equals(
                        [IO.Path]::GetFullPath($expectedBackup),
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    -not (Test-Path -LiteralPath $backupPath -PathType Leaf) -or
                    (Get-SHA256 -Path $backupPath) -ne [string]$state.backup_sha256) {
                    throw "Original LEA provider backup is missing or changed"
                }
                Move-Item -LiteralPath $backupPath -Destination $providerDestination
                $backupRestored = $true
            }
        }

        if (Test-Path -LiteralPath $profileDirectory -PathType Container) {
            if ([bool]$state.profile_directory_created) {
                Remove-Item -LiteralPath $profileDirectory -Recurse -Force
                Write-Host "INFO Cached tunnel profiles were removed and are not recoverable"
            }
            elseif ($state.previous_profile_acl_sddl) {
                Restore-DirectoryAcl `
                    -Path $profileDirectory `
                    -Sddl ([string]$state.previous_profile_acl_sddl)
            }
        }
        $baseDirectory = [IO.Path]::GetFullPath([string]$state.base_directory)
        if ([bool]$state.base_directory_created -and
            (Test-Path -LiteralPath $baseDirectory -PathType Container) -and
            @(Get-ChildItem -LiteralPath $baseDirectory -Force).Count -eq 0) {
            Remove-Item -LiteralPath $baseDirectory -Force
        }

        Remove-Item -LiteralPath $statePath -Force
        if ($removedProvider -and (Test-Path -LiteralPath $removedProvider)) {
            Remove-Item -LiteralPath $removedProvider -Force
        }
        Write-Host "PASS Secuway Windows support removed"
        Write-Host "INFO Official OpenVPN and its Interactive Service were left installed"
        if (-not [bool]$state.service_was_running) {
            Write-Host "INFO OpenVPN Interactive Service was restored to its original stopped state"
        }
    }
    catch {
        if ($removedProvider -and (Test-Path -LiteralPath $removedProvider)) {
            if ($backupRestored -and
                $backupPath -and
                (Test-Path -LiteralPath $providerDestination) -and
                -not (Test-Path -LiteralPath $backupPath)) {
                Move-Item -LiteralPath $providerDestination -Destination $backupPath
            }
            if (-not (Test-Path -LiteralPath $providerDestination)) {
                Move-Item -LiteralPath $removedProvider -Destination $providerDestination -Force
            }
        }
        if ($serviceStopped) {
            Start-Service -Name $serviceName -ErrorAction SilentlyContinue
        }
        throw
    }
}

if (-not (Test-IsWindows)) {
    throw "This installer must run on Windows"
}
if (-not [Environment]::Is64BitProcess) {
    throw "Use 64-bit PowerShell for this installer"
}

if (-not $TargetSid) {
    $TargetSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}
try {
    $userSid = [Security.Principal.SecurityIdentifier]::new($TargetSid)
}
catch {
    throw "TargetSid is not a valid Windows SID"
}

if ($Action -eq "Install") {
    if (-not $LeaProviderPath) {
        throw "-LeaProviderPath is required for Install"
    }
    $LeaProviderPath = [IO.Path]::GetFullPath($LeaProviderPath)
}

if ($Action -eq "Status") {
    try {
        $setting = Get-OpenVPNSetting
        $statePath = Join-Path $setting.ModuleDirectory $stateFileName
        $state = $null
        if (Test-Path -LiteralPath $statePath -PathType Leaf) {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        }
        $statusProfileDirectory = if ($state) {
            [string]$state.profile_directory
        }
        else {
            Join-Path (Join-Path $setting.ConfigDirectory "mrcha-secuway") $userSid.Value
        }
        $providerPath = Join-Path $setting.ModuleDirectory $providerFileName
        $providerPresent = Test-Path -LiteralPath $providerPath -PathType Leaf
        $providerHashMatches = $false
        if ($providerPresent -and $state) {
            $providerHashMatches = (
                (Get-SHA256 -Path $providerPath) -eq
                [string]$state.installed_sha256
            )
        }
        $profilePresent = Test-Path -LiteralPath $statusProfileDirectory -PathType Container
        $profileAclProtected = $false
        if ($profilePresent) {
            $profileAclProtected = (Get-Acl -LiteralPath $statusProfileDirectory).AreAccessRulesProtected
        }
        $serviceStatus = (Get-Service -Name $serviceName).Status.ToString()
        $installStatus = if (-not $state) {
            "NOT_INSTALLED"
        }
        elseif ($providerHashMatches -and $profileAclProtected -and $serviceStatus -eq "Running") {
            "READY"
        }
        else {
            "BROKEN"
        }
        $status = [ordered]@{
            schema = "secuway-windows-install-status/v1"
            status = $installStatus
            architecture = $setting.Architecture
            openvpn_signed = $true
            service_status = $serviceStatus
            provider_present = $providerPresent
            provider_hash_matches = $providerHashMatches
            target_profile_acl_present = $profilePresent
            target_profile_acl_protected = $profileAclProtected
            validation_scope = "local-install"
            secrets_printed = $false
        }
        $status | ConvertTo-Json -Compress
    }
    catch {
        [ordered]@{
            schema = "secuway-windows-install-status/v1"
            status = "OPENVPN_NOT_READY"
            error = $_.Exception.Message
            secrets_printed = $false
        } | ConvertTo-Json -Compress
        exit 1
    }
    exit 0
}

if (-not (Test-IsAdministrator)) {
    if ($Elevated) {
        throw "Elevation was requested but the new process is not an administrator"
    }
    Invoke-SelfElevated `
        -RequestedAction $Action `
        -RequestedSid $userSid.Value `
        -RequestedProvider $LeaProviderPath
}

$openVPNSetting = Get-OpenVPNSetting
switch ($Action) {
    "Install" {
        Install-SecuwayWindowsSupport `
            -Setting $openVPNSetting `
            -ProviderSource $LeaProviderPath `
            -UserSid $userSid
    }
    "Uninstall" {
        Uninstall-SecuwayWindowsSupport `
            -Setting $openVPNSetting `
            -UserSid $userSid
    }
}

exit 0

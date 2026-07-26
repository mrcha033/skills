[CmdletBinding()]
param(
    [ValidateSet("Preflight", "Tunnel")]
    [string]$Mode = "Preflight",

    [string]$ConfigPath,

    [string]$ProbeHost,

    [ValidateRange(1, 65535)]
    [int]$ProbePort = 22,

    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 90,

    [switch]$RequireNonAdmin,

    [switch]$KeepConnected,

    [switch]$KeepLog
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-OpenVPNSetting {
    $registryPath = "HKLM:\SOFTWARE\OpenVPN"
    if (-not (Test-Path $registryPath)) {
        throw "OpenVPN registry settings are not installed"
    }
    $settings = Get-Item -Path $registryPath
    $installDirectory = [string]$settings.GetValue("")
    if (-not $installDirectory) {
        throw "OpenVPN install directory is missing from the registry"
    }
    $configDirectory = [string]$settings.GetValue("config_dir")
    if (-not $configDirectory) {
        $configDirectory = Join-Path $installDirectory "config"
    }
    $executable = [string]$settings.GetValue("exe_path")
    if (-not $executable) {
        $executable = Join-Path $installDirectory "bin\openvpn.exe"
    }
    return [pscustomobject]@{
        InstallDirectory = $installDirectory
        ConfigDirectory = [System.IO.Path]::GetFullPath($configDirectory)
        Executable = [System.IO.Path]::GetFullPath($executable)
        ProviderDirectory = Join-Path $installDirectory "ssl\modules"
    }
}

function Test-ConfigApprovedForUnprivilegedService {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$ConfigDirectory
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($ConfigDirectory)
    if (-not $root.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $root += [System.IO.Path]::DirectorySeparatorChar
    }
    return $fullPath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
}

function Read-ServiceMessage {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.Pipes.NamedPipeClientStream]$Pipe,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutMilliseconds
    )

    $memory = New-Object System.IO.MemoryStream
    try {
        do {
            $buffer = New-Object byte[] 4096
            $readTask = $Pipe.ReadAsync($buffer, 0, $buffer.Length)
            if (-not $readTask.Wait($TimeoutMilliseconds)) {
                throw "Timed out waiting for OpenVPN Interactive Service"
            }
            $count = $readTask.Result
            if ($count -eq 0) {
                throw "OpenVPN Interactive Service closed the pipe"
            }
            $memory.Write($buffer, 0, $count)
        } while (-not $Pipe.IsMessageComplete)
        return [Text.Encoding]::Unicode.GetString($memory.ToArray())
    }
    finally {
        $memory.Dispose()
    }
}

function Open-InteractiveServiceSession {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$Options,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutMilliseconds
    )

    $pipe = [System.IO.Pipes.NamedPipeClientStream]::new(
        ".",
        "openvpn\service",
        [System.IO.Pipes.PipeDirection]::InOut,
        [System.IO.Pipes.PipeOptions]::Asynchronous
    )
    try {
        $pipe.Connect($TimeoutMilliseconds)
        $pipe.ReadMode = [System.IO.Pipes.PipeTransmissionMode]::Message

        # The service contract requires exactly three UTF-16 strings, separated
        # by NULs, written as one message: working-dir, options, stdin.
        $startup = "$WorkingDirectory`0$Options`0`0"
        $bytes = [Text.Encoding]::Unicode.GetBytes($startup)
        $pipe.Write($bytes, 0, $bytes.Length)
        $pipe.Flush()
        [Array]::Clear($bytes, 0, $bytes.Length)

        $response = Read-ServiceMessage -Pipe $pipe -TimeoutMilliseconds $TimeoutMilliseconds
        if ($response -notmatch "^0x00000000`n0x([0-9A-Fa-f]{8})`nProcess ID") {
            $firstLine = ($response -split "`n", 2)[0]
            throw "Interactive Service rejected OpenVPN startup ($firstLine)"
        }
        $processId = [Convert]::ToInt32($Matches[1], 16)
        return [pscustomobject]@{
            Pipe = $pipe
            ProcessId = $processId
        }
    }
    catch {
        $pipe.Dispose()
        throw
    }
}

if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
    throw "This smoke test must run on Windows."
}

$administrator = Test-IsAdministrator
if ($RequireNonAdmin -and $administrator) {
    throw "This run is elevated; it cannot prove the non-admin runtime contract"
}

$service = Get-Service -Name "OpenVPNServiceInteractive" -ErrorAction Stop
if ($service.Status -ne "Running") {
    throw "OpenVPNServiceInteractive is installed but not running"
}

$settings = Get-OpenVPNSetting
if (-not (Test-Path -LiteralPath $settings.Executable -PathType Leaf)) {
    throw "OpenVPN executable from the service registry is missing"
}
$leaProvider = Join-Path $settings.ProviderDirectory "lea.dll"
if (-not (Test-Path -LiteralPath $leaProvider -PathType Leaf)) {
    throw "LEA provider is not installed in the OpenVPN module directory"
}

Write-Host "PASS OpenVPN Interactive Service is running"
Write-Host "PASS OpenVPN executable is installed"
Write-Host "PASS LEA provider is installed"
Write-Host ("PASS caller token: " + $(if ($administrator) { "elevated" } else { "non-admin" }))

if ($Mode -eq "Preflight") {
    exit 0
}

if (-not $ConfigPath) {
    throw "-ConfigPath is required for Tunnel mode"
}
$ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Tunnel profile does not exist"
}
if (-not (Test-ConfigApprovedForUnprivilegedService -Path $ConfigPath -ConfigDirectory $settings.ConfigDirectory)) {
    throw "Tunnel profile is outside the Interactive Service config_dir; provision it during the one-time installer step"
}

$requiredDirectives = @{
    "providers" = "^\s*providers\s+lea\s+default\s*(?:[#;].*)?$"
    "disable-dco" = "^\s*disable-dco\s*(?:[#;].*)?$"
    "tap-windows6" = "^\s*windows-driver\s+tap-windows6\s*(?:[#;].*)?$"
}
$configLines = Get-Content -LiteralPath $ConfigPath
foreach ($directive in $requiredDirectives.GetEnumerator()) {
    if (-not ($configLines -match $directive.Value)) {
        throw "Tunnel profile is missing required Windows directive: $($directive.Key)"
    }
}
$configLines = $null

$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("secuway-windows-smoke-" + [Guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($temporaryDirectory) | Out-Null
$logPath = Join-Path $temporaryDirectory "openvpn.log"
$escapedConfig = $ConfigPath.Replace('"', '\"')
$escapedLog = $logPath.Replace('"', '\"')
$options = "--config `"$escapedConfig`" --log `"$escapedLog`" --verb 3"
$session = $null
$connected = $false

try {
    $session = Open-InteractiveServiceSession `
        -WorkingDirectory (Split-Path -Parent $ConfigPath) `
        -Options $options `
        -TimeoutMilliseconds ($TimeoutSeconds * 1000)

    Write-Host "PASS Interactive Service accepted the non-admin startup message"
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $process = Get-Process -Id $session.ProcessId -ErrorAction SilentlyContinue
        if (-not $process) {
            throw "OpenVPN exited before establishing the tunnel"
        }
        if (Test-Path -LiteralPath $logPath) {
            $marker = Select-String -LiteralPath $logPath -Pattern "Initialization Sequence Completed" -Quiet
            if ($marker) {
                $connected = $true
                break
            }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $connected) {
        throw "OpenVPN did not establish a tunnel before the timeout"
    }
    Write-Host "PASS OpenVPN tunnel initialization completed"

    if ($ProbeHost) {
        $probe = Test-NetConnection -ComputerName $ProbeHost -Port $ProbePort -InformationLevel Quiet
        if (-not $probe) {
            throw "Tunnel came up but the designated internal endpoint was unreachable"
        }
        Write-Host "PASS Designated internal endpoint is reachable"
    }
    else {
        Write-Host "PASS Tunnel-only smoke completed; no internal endpoint probe was requested"
    }

    if ($KeepConnected) {
        $session.Pipe.Dispose()
        $KeepLog = $true
        Write-Host "INFO OpenVPN tunnel left connected by request"
        $session = $null
    }
}
finally {
    if ($session) {
        Stop-Process -Id $session.ProcessId -ErrorAction SilentlyContinue
        Wait-Process -Id $session.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
        $session.Pipe.Dispose()
    }
    if (-not $KeepLog -and (Test-Path $temporaryDirectory)) {
        Remove-Item -Recurse -Force $temporaryDirectory
    }
    elseif ($KeepLog) {
        Write-Host "INFO OpenVPN log retained at $logPath"
    }
}

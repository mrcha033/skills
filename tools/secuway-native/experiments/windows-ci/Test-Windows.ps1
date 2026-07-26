[CmdletBinding()]
param(
    [ValidateSet("Portable", "Doctor")]
    [string]$Phase = "Portable",

    [ValidateSet("amd64", "arm64")]
    [string]$ExpectedArch,

    [string]$BundleRoot,

    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$experimentRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $experimentRoot
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $projectRoot)
$portableRoot = Join-Path $projectRoot "portable"

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
    }
}

function Get-NativeArchitecture {
    $architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    switch ($architecture.ToUpperInvariant()) {
        "X64" { return "amd64" }
        "ARM64" { return "arm64" }
        default { throw "Unsupported Windows architecture: $architecture" }
    }
}

function Get-PEArchitecture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $stream = [System.IO.File]::OpenRead((Resolve-Path $Path))
    $reader = New-Object System.IO.BinaryReader($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) {
            throw "Not a PE executable: $Path"
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadInt32()
        if ($peOffset -lt 0x40 -or $peOffset -gt ($stream.Length - 6)) {
            throw "Invalid PE header offset: $Path"
        }
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) {
            throw "Missing PE signature: $Path"
        }
        $machine = $reader.ReadUInt16()
        switch ($machine) {
            0x8664 { return "amd64" }
            0xAA64 { return "arm64" }
            default { return ("unknown-0x{0:X4}" -f $machine) }
        }
    }
    finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Assert-PEArchitecture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Architecture
    )

    $actual = Get-PEArchitecture -Path $Path
    if ($actual -ne $Architecture) {
        throw "PE architecture mismatch for $Path`: expected $Architecture, got $actual"
    }
    Write-Host "PASS PE architecture: $Architecture ($Path)"
}

function Invoke-Captured {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$Arguments = @()
    )

    $output = & $FilePath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) {
        $output | ForEach-Object { Write-Host $_ }
    }
    if ($exitCode -ne 0) {
        throw "Command failed with exit code $exitCode`: $FilePath"
    }
    return ($output -join [Environment]::NewLine)
}

if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
    throw "This harness must run on Windows."
}

$nativeArch = Get-NativeArchitecture
if ($ExpectedArch -and $ExpectedArch -ne $nativeArch) {
    throw "Runner architecture mismatch: expected $ExpectedArch, got $nativeArch"
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "build\windows-ci\$nativeArch"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null

switch ($Phase) {
    "Portable" {
        $go = (Get-Command go -ErrorAction Stop).Source
        $binary = Join-Path $OutputDirectory "secuway.exe"
        $testAppData = Join-Path $OutputDirectory "appdata"
        if (Test-Path $testAppData) {
            Remove-Item -Recurse -Force $testAppData
        }
        [System.IO.Directory]::CreateDirectory($testAppData) | Out-Null

        Push-Location $portableRoot
        try {
            $env:CGO_ENABLED = "0"
            $env:GOOS = "windows"
            $env:GOARCH = $nativeArch

            Invoke-Native -FilePath $go -Arguments @(
                "test", "./internal/store",
                "-run", "^TestDPAPIRoundTrip$",
                "-count=1",
                "-v"
            )
            Invoke-Native -FilePath $go -Arguments @("test", "./...", "-count=1")
            Invoke-Native -FilePath $go -Arguments @(
                "build",
                "-buildvcs=false",
                "-trimpath",
                "-ldflags=-s -w -buildid=",
                "-o", $binary,
                "./cmd/secuway"
            )
        }
        finally {
            Pop-Location
        }

        Assert-PEArchitecture -Path $binary -Architecture $nativeArch
        $bundledCli = Join-Path $repositoryRoot (
            "skills\secuway-vpn\assets\windows-$nativeArch\secuway.exe"
        )
        if (Test-Path -LiteralPath $bundledCli -PathType Leaf) {
            Assert-PEArchitecture -Path $bundledCli -Architecture $nativeArch
            $builtHash = (
                Get-FileHash -LiteralPath $binary -Algorithm SHA256
            ).Hash
            $bundledHash = (
                Get-FileHash -LiteralPath $bundledCli -Algorithm SHA256
            ).Hash
            if ($builtHash -ne $bundledHash) {
                throw "Bundled CLI differs from the pinned Go source build"
            }
            Write-Host "PASS Bundled CLI reproducibility: $nativeArch"
        }
        $version = Invoke-Captured -FilePath $binary -Arguments @("version")
        if ($version -notmatch "windows/$nativeArch") {
            throw "Version output did not identify windows/$nativeArch"
        }

        $oldAppData = $env:APPDATA
        try {
            $env:APPDATA = $testAppData
            $statusText = & $binary status --server https://test.invalid --json
            if ($LASTEXITCODE -ne 0) {
                throw "Synthetic status command failed"
            }
            $status = $statusText | ConvertFrom-Json
            if ($status.status -ne "NEEDS_ENROLLMENT") {
                throw "Fresh Windows store should report NEEDS_ENROLLMENT"
            }
            if ($status.credential_store -ne "windows-dpapi") {
                throw "Unexpected credential store: $($status.credential_store)"
            }
            if ($status.secrets_printed -ne $false) {
                throw "Status contract says secrets were printed"
            }
            Invoke-Native -FilePath $binary -Arguments @(
                "forget", "--server", "https://test.invalid"
            )
        }
        finally {
            $env:APPDATA = $oldAppData
            if (Test-Path $testAppData) {
                Remove-Item -Recurse -Force $testAppData
            }
        }

        Write-Host "PASS Windows portable runtime: $nativeArch"
        Write-Host "PASS Windows DPAPI synthetic round trip: $nativeArch"
        Write-Host "PASS Fresh-store status contract: $nativeArch"
        Write-Host "ARTIFACT $binary"
    }

    "Doctor" {
        if (-not $BundleRoot) {
            throw "-BundleRoot is required for the Doctor phase"
        }
        $BundleRoot = [System.IO.Path]::GetFullPath($BundleRoot)
        $binary = Join-Path $BundleRoot "bin\secuway.exe"
        $openvpn = Join-Path $BundleRoot "libexec\openvpn.exe"
        $provider = Join-Path $BundleRoot "lib\ossl-modules\lea.dll"

        foreach ($required in @($binary, $openvpn, $provider)) {
            if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
                throw "Incomplete Windows bundle; missing $required"
            }
            Assert-PEArchitecture -Path $required -Architecture $nativeArch
        }

        $doctor = Invoke-Captured -FilePath $binary -Arguments @("doctor")
        foreach ($marker in @(
            "OK  cipher: LEA-128-CBC",
            "OK  compression: LZO",
            "OK  platform: windows/$nativeArch"
        )) {
            if ($doctor -notmatch [regex]::Escape($marker)) {
                throw "Doctor output is missing: $marker"
            }
        }

        Write-Host "PASS Windows LEA/OpenVPN doctor: $nativeArch"
    }
}

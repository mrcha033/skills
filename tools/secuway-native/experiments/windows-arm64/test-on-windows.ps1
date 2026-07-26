[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Provider,

    [Parameter(Mandatory = $true)]
    [string] $ProviderSmoke,

    [Parameter(Mandatory = $true)]
    [string] $OpenVpnRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Convert-HexToByteArray {
    param([Parameter(Mandatory = $true)][string] $Hex)

    if (($Hex.Length % 2) -ne 0) {
        throw 'hex input must contain an even number of characters'
    }

    $bytes = [byte[]]::new($Hex.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        $bytes[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16)
    }
    return $bytes
}

function Get-PeMachine {
    param([Parameter(Mandatory = $true)][string] $Path)

    $stream = [IO.File]::OpenRead($Path)
    try {
        $reader = [IO.BinaryReader]::new($stream)
        if ($reader.ReadUInt16() -ne 0x5A4D) {
            throw "$Path is not an MZ executable"
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadUInt32()
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) {
            throw "$Path has no PE signature"
        }
        return $reader.ReadUInt16()
    }
    finally {
        $stream.Dispose()
    }
}

$Provider = (Resolve-Path $Provider).Path
$ProviderSmoke = (Resolve-Path $ProviderSmoke).Path
$OpenVpnRoot = (Resolve-Path $OpenVpnRoot).Path
$OpenSsl = Join-Path $OpenVpnRoot 'bin\openssl.exe'
$OpenVpn = Join-Path $OpenVpnRoot 'bin\openvpn.exe'

if ((Get-PeMachine -Path $Provider) -ne 0xAA64) {
    throw 'lea.dll is not IMAGE_FILE_MACHINE_ARM64'
}
if ((Get-PeMachine -Path $ProviderSmoke) -ne 0xAA64) {
    throw 'provider_smoke.exe is not IMAGE_FILE_MACHINE_ARM64'
}
if ((Get-PeMachine -Path $OpenSsl) -ne 0xAA64) {
    throw 'official openssl.exe is not IMAGE_FILE_MACHINE_ARM64'
}
if ((Get-PeMachine -Path $OpenVpn) -ne 0xAA64) {
    throw 'official openvpn.exe is not IMAGE_FILE_MACHINE_ARM64'
}

$Dumpbin = (Get-Command dumpbin.exe -ErrorAction Stop).Source
$Exports = (& $Dumpbin /nologo /exports $Provider 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "dumpbin /exports failed:`n$Exports"
}
$ExportNames = @(
    [regex]::Matches(
        $Exports,
        '(?m)^\s+\d+\s+[0-9A-F]+\s+[0-9A-F]+\s+(\S+)\s*$'
    ) | ForEach-Object { $_.Groups[1].Value }
)
if ($ExportNames.Count -ne 1 -or $ExportNames[0] -ne 'OSSL_provider_init') {
    throw "unexpected provider exports: $($ExportNames -join ', ')"
}

$Imports = (& $Dumpbin /nologo /imports $Provider 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "dumpbin /imports failed:`n$Imports"
}
if ($Imports -notmatch '(?im)^\s*libcrypto-3-arm64\.dll\s*$') {
    throw 'lea.dll does not import libcrypto-3-arm64.dll'
}
if ($Imports -match '(?im)^\s*(libc\+\+|libunwind|libgcc)[^\s]*\.dll\s*$') {
    throw 'lea.dll depends on an unbundled compiler runtime DLL'
}

$SmokeImports = (& $Dumpbin /nologo /imports $ProviderSmoke 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "dumpbin /imports on provider_smoke.exe failed:`n$SmokeImports"
}
if ($SmokeImports -notmatch '(?im)^\s*libcrypto-3-arm64\.dll\s*$') {
    throw 'provider_smoke.exe does not import libcrypto-3-arm64.dll'
}

$Version = (& $OpenSsl version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^OpenSSL 3\.6\.3\b') {
    throw "official OpenSSL ABI mismatch: $Version"
}

$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("secuway-lea-" + [Guid]::NewGuid())
$ModuleDir = Join-Path $TempRoot 'modules'
$PlainPath = Join-Path $TempRoot 'plain.bin'
$CipherPath = Join-Path $TempRoot 'cipher.bin'
$RoundTripPath = Join-Path $TempRoot 'roundtrip.bin'
$SmokeInOpenVpnBin = Join-Path $OpenVpnRoot 'bin\secuway-provider-smoke.exe'
$SavedModules = $env:OPENSSL_MODULES

try {
    New-Item -ItemType Directory -Path $ModuleDir -Force | Out-Null
    Copy-Item $Provider (Join-Path $ModuleDir 'lea.dll')
    Copy-Item $ProviderSmoke $SmokeInOpenVpnBin -Force
    $env:OPENSSL_MODULES = $ModuleDir

    $CipherList = (
        & $OpenSsl list `
            -provider-path $ModuleDir `
            -provider lea `
            -provider default `
            -cipher-algorithms 2>&1 | Out-String
    )
    if ($LASTEXITCODE -ne 0 -or $CipherList -notmatch '(?i)\bLEA-128-CBC\b') {
        throw "OpenSSL failed to load LEA-128-CBC:`n$CipherList"
    }

    $SmokeOutput = (& $SmokeInOpenVpnBin 2>&1 | Out-String)
    if (
        $LASTEXITCODE -ne 0 -or
        $SmokeOutput -notmatch 'LEA-128-CBC KAT encrypt=PASS decrypt=PASS'
    ) {
        throw "provider_smoke.exe failed:`n$SmokeOutput"
    }
    Write-Output ($SmokeOutput.Trim())

    # First Crypto++ LEA-128 ECB reference vector. One CBC block with a
    # zero IV is identical to ECB and exercises the provider's CBC path.
    $Key = '07AB6305B025D83F79ADDAA63AC8AD00'
    $Iv = '00000000000000000000000000000000'
    $Plain = 'F28AE3256AAD23B415E028063B610C60'
    $ExpectedCipher = '64D908FCB7EBFEF90FD670106DE7C7C5'
    [IO.File]::WriteAllBytes($PlainPath, (Convert-HexToByteArray $Plain))

    $EncOutput = (
        & $OpenSsl enc `
            -e -LEA-128-CBC `
            -provider-path $ModuleDir `
            -provider lea `
            -provider default `
            -K $Key -iv $Iv -nopad `
            -in $PlainPath -out $CipherPath 2>&1 | Out-String
    )
    if ($LASTEXITCODE -ne 0) {
        throw "LEA encryption failed:`n$EncOutput"
    }
    $ActualCipher = [Convert]::ToHexString([IO.File]::ReadAllBytes($CipherPath))
    if ($ActualCipher -ne $ExpectedCipher) {
        throw "LEA KAT mismatch: expected $ExpectedCipher, got $ActualCipher"
    }

    $DecOutput = (
        & $OpenSsl enc `
            -d -LEA-128-CBC `
            -provider-path $ModuleDir `
            -provider lea `
            -provider default `
            -K $Key -iv $Iv -nopad `
            -in $CipherPath -out $RoundTripPath 2>&1 | Out-String
    )
    if ($LASTEXITCODE -ne 0) {
        throw "LEA decryption failed:`n$DecOutput"
    }
    $RoundTrip = [Convert]::ToHexString([IO.File]::ReadAllBytes($RoundTripPath))
    if ($RoundTrip -ne $Plain) {
        throw "LEA decrypt mismatch: expected $Plain, got $RoundTrip"
    }

    $OpenVpnCiphers = (
        & $OpenVpn --providers lea default --show-ciphers 2>&1 | Out-String
    )
    if ($LASTEXITCODE -ne 0 -or $OpenVpnCiphers -notmatch '(?i)\bLEA-128-CBC\b') {
        throw "official OpenVPN did not expose LEA-128-CBC:`n$OpenVpnCiphers"
    }
}
finally {
    $env:OPENSSL_MODULES = $SavedModules
    if (Test-Path $SmokeInOpenVpnBin) {
        Remove-Item -Force $SmokeInOpenVpnBin
    }
    if (Test-Path $TempRoot) {
        Remove-Item -Recurse -Force $TempRoot
    }
}

Write-Output 'WINDOWS_ARM64_PE=PASS'
Write-Output 'OPENSSL_3_6_3_PROVIDER_LOAD=PASS'
Write-Output 'PROVIDER_SMOKE_KAT=PASS'
Write-Output 'LEA_128_CBC_KAT=PASS'
Write-Output 'LEA_128_CBC_ROUNDTRIP=PASS'
Write-Output 'OPENVPN_2_7_5_CIPHER_DISCOVERY=PASS'

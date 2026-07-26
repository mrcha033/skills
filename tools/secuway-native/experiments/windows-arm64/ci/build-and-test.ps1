[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExperimentRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$NativeRoot = (Resolve-Path (Join-Path $ExperimentRoot '..\..')).Path
$RepositoryRoot = (Resolve-Path (Join-Path $NativeRoot '..\..')).Path
$BuildRoot = Join-Path $ExperimentRoot 'build-ci'
$DownloadRoot = Join-Path $ExperimentRoot 'downloads-ci'
$ExtractRoot = Join-Path $BuildRoot 'official-openvpn'
$VcpkgRoot = Join-Path $BuildRoot 'vcpkg'
$DistRoot = Join-Path $ExperimentRoot 'dist-ci'
$MsiName = 'OpenVPN-2.7.5-I001-arm64.msi'
$MsiUrl = "https://build.openvpn.net/downloads/releases/$MsiName"
$MsiSha256 = '425d0b87651a7c58e3f8e6ffed0fa0b7d6f5fc45d51b654a337e8bda1b2dd211'
$VcpkgCommit = 'a7eda31dc16994fcaa8587982eb833a8695f1b6f'

if ($env:PROCESSOR_ARCHITECTURE -ne 'ARM64') {
    throw "native Windows ARM64 runner required; got $env:PROCESSOR_ARCHITECTURE"
}

New-Item -ItemType Directory -Path $BuildRoot, $DownloadRoot, $DistRoot -Force | Out-Null

$MsiPath = Join-Path $DownloadRoot $MsiName
if (-not (Test-Path $MsiPath)) {
    Invoke-WebRequest -Uri $MsiUrl -OutFile $MsiPath
}
$ActualMsiSha256 = (Get-FileHash -Algorithm SHA256 $MsiPath).Hash.ToLowerInvariant()
if ($ActualMsiSha256 -ne $MsiSha256) {
    throw "official MSI hash mismatch: $ActualMsiSha256"
}
$MsiSignature = Get-AuthenticodeSignature $MsiPath
if ($MsiSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "official MSI Authenticode validation failed: $($MsiSignature.Status)"
}

if (Test-Path $ExtractRoot) {
    Remove-Item -Recurse -Force $ExtractRoot
}
New-Item -ItemType Directory -Path $ExtractRoot | Out-Null
$MsiProcess = Start-Process `
    -FilePath (Join-Path $env:SystemRoot 'System32\msiexec.exe') `
    -ArgumentList @(
        '/a',
        "`"$MsiPath`"",
        '/qn',
        "TARGETDIR=`"$ExtractRoot`""
    ) `
    -Wait `
    -PassThru
if ($MsiProcess.ExitCode -ne 0) {
    throw (
        'msiexec administrative extraction failed with exit code ' +
        $MsiProcess.ExitCode
    )
}

if (-not (Test-Path $VcpkgRoot)) {
    $cloned = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        & git clone `
            https://github.com/microsoft/vcpkg.git `
            $VcpkgRoot
        if ($LASTEXITCODE -eq 0) {
            $cloned = $true
            break
        }
        if (Test-Path -LiteralPath $VcpkgRoot) {
            Remove-Item -LiteralPath $VcpkgRoot -Recurse -Force
        }
        if ($attempt -lt 3) {
            Write-Warning "vcpkg clone attempt $attempt failed; retrying"
            Start-Sleep -Seconds (5 * $attempt)
        }
    }
    if (-not $cloned) {
        throw 'vcpkg clone failed'
    }
}
& git -C $VcpkgRoot checkout --detach $VcpkgCommit
if ($LASTEXITCODE -ne 0) {
    throw 'vcpkg checkout failed'
}
& (Join-Path $VcpkgRoot 'bootstrap-vcpkg.bat') -disableMetrics
if ($LASTEXITCODE -ne 0) {
    throw 'vcpkg bootstrap failed'
}

$CmakeBuild = Join-Path $BuildRoot 'cmake'
$configured = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    & cmake `
        -S $ExperimentRoot `
        -B $CmakeBuild `
        -A ARM64 `
        "-DCMAKE_TOOLCHAIN_FILE=$(Join-Path $VcpkgRoot 'scripts\buildsystems\vcpkg.cmake')" `
        '-DVCPKG_TARGET_TRIPLET=arm64-windows-secuway' `
        "-DVCPKG_OVERLAY_TRIPLETS=$(Join-Path $ExperimentRoot 'triplets')"
    if ($LASTEXITCODE -eq 0) {
        $configured = $true
        break
    }
    if ($attempt -lt 3) {
        Write-Warning "CMake/vcpkg configure attempt $attempt failed; retrying"
        Start-Sleep -Seconds (5 * $attempt)
    }
}
if (-not $configured) {
    throw 'CMake configure failed'
}
& cmake --build $CmakeBuild --config Release --parallel
if ($LASTEXITCODE -ne 0) {
    throw 'CMake build failed'
}

$Provider = Join-Path $CmakeBuild 'Release\lea.dll'
$ProviderSmoke = Join-Path $CmakeBuild 'Release\provider_smoke.exe'
if (-not (Test-Path $Provider)) {
    throw "provider output missing: $Provider"
}
if (-not (Test-Path $ProviderSmoke)) {
    throw "provider smoke output missing: $ProviderSmoke"
}
& (Join-Path $ExperimentRoot 'test-on-windows.ps1') `
    -Provider $Provider `
    -ProviderSmoke $ProviderSmoke `
    -OpenVpnRoot (Join-Path $ExtractRoot 'OpenVPN') |
    Tee-Object -FilePath (Join-Path $DistRoot 'runtime-evidence.txt')

$BundledRoot = Join-Path (
    $RepositoryRoot
) 'skills\secuway-vpn\assets\windows-arm64'
$BundledProvider = Join-Path $BundledRoot 'lea.dll'
$BundledSmoke = Join-Path $BundledRoot 'provider_smoke.exe'
$AssetManifestPath = Join-Path (
    $RepositoryRoot
) 'skills\secuway-vpn\assets\manifest.json'
$AssetManifest = Get-Content -LiteralPath $AssetManifestPath -Raw |
    ConvertFrom-Json
foreach ($BundledAsset in @(
        @{ Relative = 'windows-arm64/lea.dll'; Path = $BundledProvider },
        @{
            Relative = 'windows-arm64/provider_smoke.exe'
            Path = $BundledSmoke
        }
    )) {
    $Record = $AssetManifest.assets.PSObject.Properties[
        $BundledAsset.Relative
    ].Value
    $ActualHash = (
        Get-FileHash -LiteralPath $BundledAsset.Path -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($ActualHash -ne [string]$Record.sha256) {
        throw "bundled ARM64 asset hash mismatch: $($BundledAsset.Relative)"
    }
}
& (Join-Path $ExperimentRoot 'test-on-windows.ps1') `
    -Provider $BundledProvider `
    -ProviderSmoke $BundledSmoke `
    -OpenVpnRoot (Join-Path $ExtractRoot 'OpenVPN') |
    Tee-Object -FilePath (Join-Path $DistRoot 'bundled-runtime-evidence.txt')

Copy-Item $Provider (Join-Path $DistRoot 'lea.dll') -Force
Copy-Item $ProviderSmoke (Join-Path $DistRoot 'provider_smoke.exe') -Force
$ProviderSourceHash = (
    Get-FileHash -Algorithm SHA256 (Join-Path $NativeRoot 'src\lea_provider.cpp')
).Hash.ToLowerInvariant()
$SmokeSourceHash = (
    Get-FileHash -Algorithm SHA256 (Join-Path $NativeRoot 'tests\provider_smoke.c')
).Hash.ToLowerInvariant()
Set-Content -Encoding Ascii -Path (Join-Path $DistRoot 'build-manifest.txt') -Value @(
    'schema=secuway-windows-arm64-provider-build/v1'
    'target=windows-arm64-msvc'
    'openvpn_version=2.7.5-I001'
    'openssl_version=3.6.3'
    'cryptopp_version=8.9.0'
    'cryptopp_vcpkg_port_version=2'
    'cryptopp_vcpkg_git_tree=7a43c1863687809d90c65c768b70eb0add5aacc6'
    "openvpn_msi_sha256=$MsiSha256"
    "vcpkg_commit=$VcpkgCommit"
    "provider_source_sha256=$ProviderSourceHash"
    "provider_smoke_source_sha256=$SmokeSourceHash"
    'native_runtime_validated=true'
    "bundled_provider_sha256=$(
        (Get-FileHash -Algorithm SHA256 $BundledProvider).Hash.ToLowerInvariant()
    )"
    "bundled_provider_smoke_sha256=$(
        (Get-FileHash -Algorithm SHA256 $BundledSmoke).Hash.ToLowerInvariant()
    )"
    'bundled_native_runtime_validated=true'
    'vendor_secuway_binaries_redistributed=false'
)
$ProviderHash = (Get-FileHash -Algorithm SHA256 (Join-Path $DistRoot 'lea.dll')).Hash.ToLowerInvariant()
$SmokeHash = (Get-FileHash -Algorithm SHA256 (Join-Path $DistRoot 'provider_smoke.exe')).Hash.ToLowerInvariant()
$ManifestHash = (Get-FileHash -Algorithm SHA256 (Join-Path $DistRoot 'build-manifest.txt')).Hash.ToLowerInvariant()
$RuntimeEvidenceHash = (
    Get-FileHash -Algorithm SHA256 (
        Join-Path $DistRoot 'runtime-evidence.txt'
    )
).Hash.ToLowerInvariant()
$BundledEvidenceHash = (
    Get-FileHash -Algorithm SHA256 (
        Join-Path $DistRoot 'bundled-runtime-evidence.txt'
    )
).Hash.ToLowerInvariant()
Set-Content -Encoding Ascii -Path (Join-Path $DistRoot 'SHA256SUMS') -Value @(
    "$ProviderHash  lea.dll"
    "$SmokeHash  provider_smoke.exe"
    "$ManifestHash  build-manifest.txt"
    "$RuntimeEvidenceHash  runtime-evidence.txt"
    "$BundledEvidenceHash  bundled-runtime-evidence.txt"
)
Write-Output "PROVIDER_SHA256=$ProviderHash"
Write-Output "PROVIDER_SMOKE_SHA256=$SmokeHash"

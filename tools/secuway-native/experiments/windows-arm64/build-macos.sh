#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$SCRIPT_DIR/versions.env"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "build-macos.sh requires macOS" >&2
    exit 2
fi

DOWNLOAD_DIR="$SCRIPT_DIR/downloads"
BUILD_DIR="$SCRIPT_DIR/build"
TOOLCHAIN_DIR="$SCRIPT_DIR/toolchain/llvm-mingw-${LLVM_MINGW_VERSION}-ucrt-macos-universal"
DIST_DIR="$SCRIPT_DIR/dist"
OPENSSL_SOURCE="$BUILD_DIR/openssl-${OPENVPN_OPENSSL_VERSION}"
CRYPTOPP_SOURCE="$BUILD_DIR/cryptopp-CRYPTOPP_${CRYPTOPP_VERSION}"
MSI_NAME="OpenVPN-${OPENVPN_VERSION}-${OPENVPN_INSTALLER_REVISION}-arm64.msi"
MSI_PATH="$DOWNLOAD_DIR/$MSI_NAME"
MSI_EXTRACT="$BUILD_DIR/msi-extracted"
OFFICIAL_CRYPTO="$MSI_EXTRACT/OpenVPN/bin/libcrypto-3-arm64.dll"
PROVIDER_SOURCE="$ROOT_DIR/src/lea_provider.cpp"
PROVIDER_SMOKE_SOURCE="$ROOT_DIR/tests/provider_smoke.c"

mkdir -p "$DOWNLOAD_DIR" "$BUILD_DIR" "$SCRIPT_DIR/toolchain" "$DIST_DIR"

download_checked() {
    url=$1
    destination=$2
    expected=$3
    if [ ! -f "$destination" ]; then
        curl -fL --retry 3 --output "$destination" "$url"
    fi
    actual=$(shasum -a 256 "$destination" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
        echo "sha256 mismatch for $destination" >&2
        echo "expected $expected" >&2
        echo "actual   $actual" >&2
        exit 1
    fi
}

download_checked "$OPENVPN_MSI_URL" "$MSI_PATH" "$OPENVPN_MSI_SHA256"
download_checked "$OPENVPN_MSI_ASC_URL" "$MSI_PATH.asc" "$OPENVPN_MSI_ASC_SHA256"
if [ ! -f "$DOWNLOAD_DIR/openvpn-security-key.asc" ]; then
    curl -fL --retry 3 --output "$DOWNLOAD_DIR/openvpn-security-key.asc" "$OPENVPN_GPG_KEY_URL"
fi
download_checked "$LLVM_MINGW_URL" \
    "$DOWNLOAD_DIR/llvm-mingw-${LLVM_MINGW_VERSION}-ucrt-macos-universal.tar.xz" \
    "$LLVM_MINGW_SHA256"
download_checked "$OPENSSL_URL" \
    "$DOWNLOAD_DIR/openssl-${OPENVPN_OPENSSL_VERSION}.tar.gz" \
    "$OPENSSL_SHA256"
download_checked "$CRYPTOPP_URL" \
    "$DOWNLOAD_DIR/cryptopp-CRYPTOPP_${CRYPTOPP_VERSION}.tar.gz" \
    "$CRYPTOPP_SHA256"

if [ ! -x "$TOOLCHAIN_DIR/bin/aarch64-w64-mingw32-clang++" ]; then
    tar -xJf "$DOWNLOAD_DIR/llvm-mingw-${LLVM_MINGW_VERSION}-ucrt-macos-universal.tar.xz" \
        -C "$SCRIPT_DIR/toolchain"
fi
export PATH="$TOOLCHAIN_DIR/bin:$PATH"

if [ ! -f "$OPENSSL_SOURCE/include/openssl/configuration.h" ]; then
    if [ ! -d "$OPENSSL_SOURCE" ]; then
        tar -xzf "$DOWNLOAD_DIR/openssl-${OPENVPN_OPENSSL_VERSION}.tar.gz" -C "$BUILD_DIR"
    fi
    (
        cd "$OPENSSL_SOURCE"
        perl ./Configure mingwarm64 no-shared no-tests no-apps no-docs \
            --cross-compile-prefix=aarch64-w64-mingw32-
    )
fi

if [ ! -f "$CRYPTOPP_SOURCE/libcryptopp.a" ]; then
    if [ ! -d "$CRYPTOPP_SOURCE" ]; then
        tar -xzf "$DOWNLOAD_DIR/cryptopp-CRYPTOPP_${CRYPTOPP_VERSION}.tar.gz" -C "$BUILD_DIR"
    fi
    (
        cd "$CRYPTOPP_SOURCE"
        make -f GNUmakefile -j8 static \
            CXX="$TOOLCHAIN_DIR/bin/aarch64-w64-mingw32-clang++" \
            AR="$TOOLCHAIN_DIR/bin/aarch64-w64-mingw32-ar" \
            RANLIB="$TOOLCHAIN_DIR/bin/aarch64-w64-mingw32-ranlib" \
            CXXFLAGS="-DNDEBUG -O2 -DCRYPTOPP_DISABLE_ASM -DWIN32_LEAN_AND_MEAN -D_WIN32_WINNT=0x0A00"
    )
fi

if [ ! -f "$OFFICIAL_CRYPTO" ]; then
    command -v docker >/dev/null 2>&1 || {
        echo "docker is required to verify and extract the official MSI" >&2
        exit 2
    }
    docker image inspect "$UBUNTU_IMAGE" >/dev/null 2>&1 || docker pull --platform linux/arm64 "$UBUNTU_IMAGE"
    docker run --rm --platform linux/arm64 \
        -e OPENVPN_GPG_FINGERPRINT="$OPENVPN_GPG_FINGERPRINT" \
        -v "$SCRIPT_DIR:/work" -w /work "$UBUNTU_IMAGE" sh -lc '
            set -eu
            apt-get update -qq
            apt-get install -y -qq gnupg msitools >/dev/null
            mkdir -m 700 /tmp/openvpn-gnupg
            gpg --homedir /tmp/openvpn-gnupg --batch \
                --import downloads/openvpn-security-key.asc >/dev/null
            gpg --homedir /tmp/openvpn-gnupg --batch --with-colons \
                --fingerprint |
                grep -F "fpr:::::::::$OPENVPN_GPG_FINGERPRINT:" >/dev/null
            gpg --homedir /tmp/openvpn-gnupg --batch \
                --verify downloads/'"$MSI_NAME"'.asc downloads/'"$MSI_NAME"'
            mkdir -p build/msi-extracted
            msiextract -C build/msi-extracted downloads/'"$MSI_NAME"' >/dev/null
        '
fi

actual_official_crypto=$(shasum -a 256 "$OFFICIAL_CRYPTO" | awk '{print $1}')
if [ "$actual_official_crypto" != "$OPENVPN_LIBCRYPTO_SHA256" ]; then
    echo "official OpenVPN libcrypto sha256 mismatch" >&2
    echo "expected $OPENVPN_LIBCRYPTO_SHA256" >&2
    echo "actual   $actual_official_crypto" >&2
    exit 1
fi

if [ ! -f "$PROVIDER_SOURCE" ]; then
    echo "provider source not found: $PROVIDER_SOURCE" >&2
    exit 2
fi
if [ ! -f "$PROVIDER_SMOKE_SOURCE" ]; then
    echo "provider smoke source not found: $PROVIDER_SMOKE_SOURCE" >&2
    exit 2
fi

"$TOOLCHAIN_DIR/bin/gendef" - "$OFFICIAL_CRYPTO" > "$BUILD_DIR/libcrypto-3-arm64.def"
"$TOOLCHAIN_DIR/bin/llvm-dlltool" \
    -m arm64 \
    -D libcrypto-3-arm64.dll \
    -d "$BUILD_DIR/libcrypto-3-arm64.def" \
    -l "$BUILD_DIR/libcrypto-3-arm64.dll.a"

"$TOOLCHAIN_DIR/bin/aarch64-w64-mingw32-clang++" \
    -std=c++17 -O2 -DNDEBUG \
    -DCRYPTOPP_DISABLE_ASM -DWIN32_LEAN_AND_MEAN -D_WIN32_WINNT=0x0A00 \
    -shared -static \
    -Wl,--gc-sections \
    -Wl,--no-insert-timestamp \
    -Wl,--exclude-all-symbols \
    -I"$OPENSSL_SOURCE/include" \
    -I"$CRYPTOPP_SOURCE" \
    -o "$BUILD_DIR/lea-unstripped.dll" \
    "$PROVIDER_SOURCE" \
    "$SCRIPT_DIR/lea.def" \
    "$CRYPTOPP_SOURCE/libcryptopp.a" \
    "$BUILD_DIR/libcrypto-3-arm64.dll.a" \
    -lbcrypt

"$TOOLCHAIN_DIR/bin/aarch64-w64-mingw32-clang" \
    -std=c11 -O2 -DNDEBUG \
    -static \
    -Wl,--gc-sections \
    -Wl,--no-insert-timestamp \
    -I"$OPENSSL_SOURCE/include" \
    -o "$BUILD_DIR/provider-smoke-unstripped.exe" \
    "$PROVIDER_SMOKE_SOURCE" \
    "$BUILD_DIR/libcrypto-3-arm64.dll.a"

"$TOOLCHAIN_DIR/bin/llvm-strip" --strip-all \
    "$BUILD_DIR/lea-unstripped.dll" \
    -o "$DIST_DIR/lea.dll"
"$TOOLCHAIN_DIR/bin/llvm-strip" --strip-all \
    "$BUILD_DIR/provider-smoke-unstripped.exe" \
    -o "$DIST_DIR/provider_smoke.exe"

"$SCRIPT_DIR/verify-pe.sh" "$DIST_DIR/lea.dll" "$DIST_DIR/provider_smoke.exe"
shasum -a 256 "$DIST_DIR/lea.dll" "$DIST_DIR/provider_smoke.exe"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
IMAGE_NAME="${SECUWAY_WINDOWS_X64_BUILD_IMAGE:-secuway-windows-x64-builder:bookworm}"

docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"
docker run --rm \
  -v "${REPO_ROOT}:/src:ro" \
  -v "${SCRIPT_DIR}:/out" \
  "${IMAGE_NAME}" -ec '
    set -euxo pipefail

    OPENVPN_VERSION=2.7.5
    OPENVPN_BUILD=I001
    OPENVPN_MSI="OpenVPN-${OPENVPN_VERSION}-${OPENVPN_BUILD}-amd64.msi"
    OPENVPN_MSI_SHA256=20a9b2831cc3be26c250caf60891c230f3bf3e1e7bd6e17b4e182f166026377a
    OPENSSL_VERSION=3.6.3
    OPENSSL_SHA256=243a86649cf6f23eeb6a2ff2456e09e5d77dd9018a54d3d96b0c6bdd6ba6c7f1
    CRYPTOPP_VERSION=8.9.0
    CRYPTOPP_TAG=CRYPTOPP_8_9_0
    CRYPTOPP_SHA256=ab5174b9b5c6236588e15a1aa1aaecb6658cdbe09501c7981ac8db276a24d9ab

    BUILD_ROOT="$(mktemp -d)"
    trap '\''rm -rf "${BUILD_ROOT}"'\'' EXIT
    mkdir -p "${BUILD_ROOT}/downloads" "${BUILD_ROOT}/msi" /out/artifacts

    curl -fsSL --retry 3 \
      "https://swupdate.openvpn.org/community/releases/${OPENVPN_MSI}" \
      -o "${BUILD_ROOT}/downloads/${OPENVPN_MSI}"
    printf "%s  %s\n" "${OPENVPN_MSI_SHA256}" "${BUILD_ROOT}/downloads/${OPENVPN_MSI}" \
      | sha256sum --check --status

    curl -fsSL --retry 3 \
      "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz" \
      -o "${BUILD_ROOT}/downloads/openssl-${OPENSSL_VERSION}.tar.gz"
    printf "%s  %s\n" "${OPENSSL_SHA256}" "${BUILD_ROOT}/downloads/openssl-${OPENSSL_VERSION}.tar.gz" \
      | sha256sum --check --status

    curl -fsSL --retry 3 \
      "https://github.com/weidai11/cryptopp/archive/refs/tags/${CRYPTOPP_TAG}.tar.gz" \
      -o "${BUILD_ROOT}/downloads/cryptopp-${CRYPTOPP_TAG}.tar.gz"
    printf "%s  %s\n" "${CRYPTOPP_SHA256}" "${BUILD_ROOT}/downloads/cryptopp-${CRYPTOPP_TAG}.tar.gz" \
      | sha256sum --check --status

    msiextract -C "${BUILD_ROOT}/msi" "${BUILD_ROOT}/downloads/${OPENVPN_MSI}" >/dev/null
    OFFICIAL_BIN="${BUILD_ROOT}/msi/OpenVPN/bin"
    OFFICIAL_CRYPTO="${OFFICIAL_BIN}/libcrypto-3-x64.dll"
    test -f "${OFFICIAL_CRYPTO}"
    x86_64-w64-mingw32-objdump -p "${OFFICIAL_CRYPTO}" \
      > "${BUILD_ROOT}/official-libcrypto.objdump"
    grep -Eq "Name[[:space:]]+[[:xdigit:]]+[[:space:]]+libcrypto-3-x64\\.dll" \
      "${BUILD_ROOT}/official-libcrypto.objdump"

    cd "${BUILD_ROOT}"
    gendef "${OFFICIAL_CRYPTO}" >/dev/null 2>&1
    test -f libcrypto-3-x64.def
    x86_64-w64-mingw32-dlltool \
      --input-def libcrypto-3-x64.def \
      --dllname libcrypto-3-x64.dll \
      --output-lib libcrypto.dll.a

    tar -xzf "${BUILD_ROOT}/downloads/openssl-${OPENSSL_VERSION}.tar.gz"
    cd "${BUILD_ROOT}/openssl-${OPENSSL_VERSION}"
    perl Configure mingw64 \
      --cross-compile-prefix=x86_64-w64-mingw32- \
      no-shared no-tests >/dev/null
    make -s build_generated

    cd "${BUILD_ROOT}"
    tar -xzf "${BUILD_ROOT}/downloads/cryptopp-${CRYPTOPP_TAG}.tar.gz"
    CRYPTOPP_DIR="${BUILD_ROOT}/cryptopp-CRYPTOPP_8_9_0"
    make -s -C "${CRYPTOPP_DIR}" -j"$(nproc)" libcryptopp.a \
      CXX=x86_64-w64-mingw32-g++ \
      AR=x86_64-w64-mingw32-ar \
      RANLIB=x86_64-w64-mingw32-ranlib \
      CXXFLAGS="-O2 -DNDEBUG -DCRYPTOPP_DISABLE_ASM"

    x86_64-w64-mingw32-g++ \
      -std=c++17 -O2 -DNDEBUG -shared \
      -I"${BUILD_ROOT}/openssl-${OPENSSL_VERSION}/include" \
      -I"${CRYPTOPP_DIR}" \
      "/src/src/lea_provider.cpp" \
      "/src/experiments/windows-x64/lea_provider.def" \
      "${CRYPTOPP_DIR}/libcryptopp.a" \
      "${BUILD_ROOT}/libcrypto.dll.a" \
      -static-libgcc -static-libstdc++ \
      -Wl,--no-insert-timestamp \
      -o /out/artifacts/lea.dll

    x86_64-w64-mingw32-gcc \
      -std=c11 -O2 \
      -I"${BUILD_ROOT}/openssl-${OPENSSL_VERSION}/include" \
      "/src/tests/provider_smoke.c" \
      "${BUILD_ROOT}/libcrypto.dll.a" \
      -Wl,--no-insert-timestamp \
      -o /out/artifacts/provider_smoke.exe

    {
      echo "schema=secuway-windows-x64-provider-build/v1"
      echo "target=x86_64-w64-mingw32"
      echo "openvpn_msi=${OPENVPN_MSI}"
      echo "openvpn_msi_sha256=${OPENVPN_MSI_SHA256}"
      echo "official_libcrypto_name=libcrypto-3-x64.dll"
      echo "official_openssl_version=${OPENSSL_VERSION}"
      echo "openssl_source_sha256=${OPENSSL_SHA256}"
      echo "cryptopp_version=${CRYPTOPP_VERSION}"
      echo "cryptopp_tag=${CRYPTOPP_TAG}"
      echo "cryptopp_source_sha256=${CRYPTOPP_SHA256}"
      echo "provider_source=src/lea_provider.cpp"
      echo "provider_source_sha256=$(sha256sum /src/src/lea_provider.cpp | awk '\''{print $1}'\'')"
      echo "provider_smoke_source=tests/provider_smoke.c"
      echo "provider_smoke_source_sha256=$(sha256sum /src/tests/provider_smoke.c | awk '\''{print $1}'\'')"
      echo "vendor_secuway_binaries_redistributed=false"
    } > /out/artifacts/build-manifest.txt

    cd /out/artifacts
    sha256sum lea.dll provider_smoke.exe build-manifest.txt > SHA256SUMS
  '

"${SCRIPT_DIR}/verify.sh"

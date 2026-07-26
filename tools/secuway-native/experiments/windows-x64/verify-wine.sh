#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${SECUWAY_WINDOWS_X64_WINE_IMAGE:-secuway-windows-x64-wine:bookworm}"
OPENVPN_MSI="OpenVPN-2.7.5-I001-amd64.msi"
OPENVPN_MSI_SHA256="20a9b2831cc3be26c250caf60891c230f3bf3e1e7bd6e17b4e182f166026377a"

docker build --platform linux/amd64 -f "${SCRIPT_DIR}/Dockerfile.wine" \
  -t "${IMAGE_NAME}" "${SCRIPT_DIR}"
docker run --rm --platform linux/amd64 \
  -e WINEDEBUG=-all \
  -v "${SCRIPT_DIR}/artifacts:/artifacts" \
  "${IMAGE_NAME}" -ec "
    set -euo pipefail

    BUILD_ROOT=\"\$(mktemp -d)\"
    trap 'rm -rf \"\${BUILD_ROOT}\"' EXIT
    mkdir -p \"\${BUILD_ROOT}/msi\" \"\${BUILD_ROOT}/wine\"

    curl -fsSL --retry 3 \
      'https://swupdate.openvpn.org/community/releases/${OPENVPN_MSI}' \
      -o \"\${BUILD_ROOT}/${OPENVPN_MSI}\"
    printf '%s  %s\n' '${OPENVPN_MSI_SHA256}' \"\${BUILD_ROOT}/${OPENVPN_MSI}\" \
      | sha256sum --check --status
    msiextract -C \"\${BUILD_ROOT}/msi\" \"\${BUILD_ROOT}/${OPENVPN_MSI}\" >/dev/null

    OPENVPN_ROOT=\"\${BUILD_ROOT}/msi/OpenVPN\"
    OPENVPN_BIN=\"\${OPENVPN_ROOT}/bin\"
    MODULES_DIR=\"\${OPENVPN_ROOT}/ssl/modules\"
    cp /artifacts/lea.dll \"\${MODULES_DIR}/lea.dll\"
    cp /artifacts/provider_smoke.exe \"\${OPENVPN_BIN}/provider_smoke.exe\"

    WINE_BIN=/usr/lib/wine/wine64
    test -x \"\${WINE_BIN}\"
    export WINEPREFIX=\"\${BUILD_ROOT}/wine\"
    export WINEARCH=win64
    MODULES_WINDOWS=\"Z:\$(printf '%s' \"\${MODULES_DIR}\" | sed 's|/|\\\\|g')\"
    export OPENSSL_MODULES=\"\${MODULES_WINDOWS}\"

    cd \"\${OPENVPN_BIN}\"
    \"\${WINE_BIN}\" wineboot --init >/dev/null 2>&1 || true
    printf '\\xF2\\x8A\\xE3\\x25\\x6A\\xAD\\x23\\xB4\\x15\\xE0\\x28\\x06\\x3B\\x61\\x0C\\x60' \
      > kat-plaintext.bin
    printf '\\x64\\xD9\\x08\\xFC\\xB7\\xEB\\xFE\\xF9\\x0F\\xD6\\x70\\x10\\x6D\\xE7\\xC7\\xC5' \
      > kat-expected.bin

    {
      echo 'schema=secuway-windows-x64-provider-runtime/v1'
      echo 'runner=wine64-on-linux-amd64'
      echo 'host_tunnel_validated=false'
      echo '[openssl-version]'
      \"\${WINE_BIN}\" ./openssl.exe version -a
      echo '[provider-list]'
      \"\${WINE_BIN}\" ./openssl.exe list \
        -provider-path \"\${MODULES_WINDOWS}\" \
        -provider lea -provider default -providers
      echo '[provider-smoke]'
      \"\${WINE_BIN}\" ./provider_smoke.exe
      echo '[lea-cbc-kat]'
      \"\${WINE_BIN}\" ./openssl.exe enc -LEA-128-CBC \
        -provider-path \"\${MODULES_WINDOWS}\" \
        -provider lea -provider default \
        -K 07AB6305B025D83F79ADDAA63AC8AD00 \
        -iv 00000000000000000000000000000000 \
        -nopad -in kat-plaintext.bin -out kat-ciphertext.bin
      cmp kat-expected.bin kat-ciphertext.bin
      echo 'kat_encrypt=pass'
      \"\${WINE_BIN}\" ./openssl.exe enc -d -LEA-128-CBC \
        -provider-path \"\${MODULES_WINDOWS}\" \
        -provider lea -provider default \
        -K 07AB6305B025D83F79ADDAA63AC8AD00 \
        -iv 00000000000000000000000000000000 \
        -nopad -in kat-ciphertext.bin -out kat-recovered.bin
      cmp kat-plaintext.bin kat-recovered.bin
      echo 'kat_decrypt=pass'
      echo '[openvpn-version]'
      \"\${WINE_BIN}\" ./openvpn.exe --version
      echo '[openvpn-lea-cipher]'
      \"\${WINE_BIN}\" ./openvpn.exe \
        --providers lea default \
        --show-ciphers
    } > /artifacts/runtime-evidence.txt 2>&1

    grep -Fq 'OpenSSL 3.6.3' /artifacts/runtime-evidence.txt
    grep -Fq 'Secuway Native LEA Provider' /artifacts/runtime-evidence.txt
    grep -Fq 'cipher=' /artifacts/runtime-evidence.txt
    grep -Fq 'LEA-128-CBC KAT encrypt=PASS decrypt=PASS' /artifacts/runtime-evidence.txt
    grep -Fq 'kat_encrypt=pass' /artifacts/runtime-evidence.txt
    grep -Fq 'kat_decrypt=pass' /artifacts/runtime-evidence.txt
    grep -Eq 'LEA-128-CBC|LEA128-CBC|LEA-CBC' /artifacts/runtime-evidence.txt
    grep -Fq 'host_tunnel_validated=false' /artifacts/runtime-evidence.txt
    cd /artifacts
    sha256sum runtime-evidence.txt > RUNTIME_SHA256SUMS
  "

sed -n '1,220p' "${SCRIPT_DIR}/artifacts/runtime-evidence.txt"

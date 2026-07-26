#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${SECUWAY_WINDOWS_X64_BUILD_IMAGE:-secuway-windows-x64-builder:bookworm}"

docker run --rm \
  -v "${SCRIPT_DIR}/artifacts:/artifacts:ro" \
  "${IMAGE_NAME}" -ec '
    set -euo pipefail
    cd /artifacts
    sha256sum --check SHA256SUMS
    file lea.dll provider_smoke.exe

    MACHINE="$(x86_64-w64-mingw32-objdump -f lea.dll | awk "/architecture:/{print \$2}")"
    test "${MACHINE}" = "i386:x86-64,"

    EXPORTS="$(x86_64-w64-mingw32-objdump -p lea.dll | sed -n "/Export Table/,\$p")"
    grep -Eq "[[:space:]]OSSL_provider_init$" <<<"${EXPORTS}"

    IMPORTS="$(x86_64-w64-mingw32-objdump -p lea.dll | sed -n "s/^[[:space:]]*DLL Name: /DLL Name: /p")"
    printf "%s\n" "${IMPORTS}"
    grep -Fxq "DLL Name: libcrypto-3-x64.dll" <<<"${IMPORTS}"
    if grep -Eq "libstdc\\+\\+|libgcc|libwinpthread" <<<"${IMPORTS}"; then
      echo "unexpected MinGW runtime DLL dependency" >&2
      exit 1
    fi
  '

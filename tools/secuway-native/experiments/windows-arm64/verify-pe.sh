#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/versions.env"

PROVIDER=${1:-"$SCRIPT_DIR/dist/lea.dll"}
PROVIDER_SMOKE=${2:-}
READOBJ="$SCRIPT_DIR/toolchain/llvm-mingw-${LLVM_MINGW_VERSION}-ucrt-macos-universal/bin/llvm-readobj"

if [ ! -x "$READOBJ" ]; then
    echo "llvm-readobj is missing; run build-macos.sh first" >&2
    exit 2
fi
if [ ! -f "$PROVIDER" ]; then
    echo "provider is missing: $PROVIDER" >&2
    exit 2
fi

REPORT=$("$READOBJ" --file-headers --coff-exports --coff-imports "$PROVIDER")

printf '%s\n' "$REPORT" | grep -q '^Format: COFF-ARM64$'
printf '%s\n' "$REPORT" | grep -q 'Machine: IMAGE_FILE_MACHINE_ARM64'
printf '%s\n' "$REPORT" | grep -q 'Name: OSSL_provider_init'
printf '%s\n' "$REPORT" | grep -q 'Name: libcrypto-3-arm64.dll'

EXPORT_COUNT=$(printf '%s\n' "$REPORT" | awk '
    /^Export \{/ { count += 1 }
    END { print count + 0 }
')
if [ "$EXPORT_COUNT" -ne 1 ]; then
    echo "expected exactly one PE export, found $EXPORT_COUNT" >&2
    exit 1
fi

if printf '%s\n' "$REPORT" | grep -Eq 'Name: (libc\+\+|libunwind|libgcc)[^ ]*\.dll'; then
    echo "provider unexpectedly depends on a toolchain runtime DLL" >&2
    exit 1
fi

echo "PE_ARM64=PASS"
echo "EXPORT_OSSL_provider_init=PASS"
echo "IMPORT_libcrypto-3-arm64.dll=PASS"
echo "TOOLCHAIN_RUNTIME_DLLS=NONE"

if [ -n "$PROVIDER_SMOKE" ]; then
    if [ ! -f "$PROVIDER_SMOKE" ]; then
        echo "provider smoke executable is missing: $PROVIDER_SMOKE" >&2
        exit 2
    fi
    SMOKE_REPORT=$("$READOBJ" --file-headers --coff-imports "$PROVIDER_SMOKE")
    printf '%s\n' "$SMOKE_REPORT" | grep -q '^Format: COFF-ARM64$'
    printf '%s\n' "$SMOKE_REPORT" |
        grep -q 'Machine: IMAGE_FILE_MACHINE_ARM64'
    printf '%s\n' "$SMOKE_REPORT" |
        grep -q 'Name: libcrypto-3-arm64.dll'
    if printf '%s\n' "$SMOKE_REPORT" |
        grep -Eq 'Name: (libc\+\+|libunwind|libgcc)[^ ]*\.dll'; then
        echo "provider smoke unexpectedly depends on a toolchain runtime DLL" >&2
        exit 1
    fi
    echo "SMOKE_PE_ARM64=PASS"
    echo "SMOKE_IMPORT_libcrypto-3-arm64.dll=PASS"
fi

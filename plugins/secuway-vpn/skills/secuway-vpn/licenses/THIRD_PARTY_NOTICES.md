# Secuway VPN third-party notices

This directory is distributed with every `secuway-vpn` skill bundle. It
contains the unmodified upstream license, patent, credit, and runtime-notice
files required for the prebuilt Windows assets under `../assets/`.

`manifest.json` is the machine-readable source of truth for exact versions,
source revisions, upstream URLs, file hashes, and binary scope.

## What is embedded in each binary

| Distributed asset | Statically included third-party code |
| --- | --- |
| `windows-amd64/secuway.exe` | Go 1.25.4 standard library, `golang.org/x/sys` v0.47.0, and `golang.org/x/term` v0.45.0 |
| `windows-arm64/secuway.exe` | Go 1.25.4 standard library, `golang.org/x/sys` v0.47.0, and `golang.org/x/term` v0.45.0 |
| `windows-amd64/lea.dll` | Crypto++ 8.9.0, GCC 12.2.0 `libstdc++`/`libgcc` runtime code, and MinGW-w64 10.0.0 runtime code |
| `windows-amd64/provider_smoke.exe` | GCC 12.2.0 support code as needed and MinGW-w64 10.0.0 runtime code |
| `windows-arm64/lea.dll` | Crypto++ 8.9.0, LLVM 22.1.8 `libc++`/`libc++abi`/`libunwind`/compiler-rt code, and MinGW-w64 runtime code from commit `c28e9555bb8800c53449f42a465ad9a5676fce88` |
| `windows-arm64/provider_smoke.exe` | LLVM 22.1.8 compiler-rt support code as needed and MinGW-w64 runtime code from commit `c28e9555bb8800c53449f42a465ad9a5676fce88` |

The AMD64 provider was produced by the pinned Debian 12 builder using the
Debian `gcc-mingw-w64` 12.2.0 package family
(`12.2.0-14+deb12u1+25.2+b1`) and MinGW-w64 `10.0.0-3`. The ARM64 provider
was produced with llvm-mingw release `20260616`, recipe commit
`170b7e1ec4ad1d9264e6ba320cd4d02f96299c60`, which pins LLVM
`llvmorg-22.1.8` and MinGW-w64 commit
`c28e9555bb8800c53449f42a465ad9a5676fce88`.

## Bundled upstream texts

- `go/LICENSE` and `go/PATENTS`: Go toolchain and standard library.
- `golang.org-x-sys/LICENSE` and `golang.org-x-sys/PATENTS`:
  `golang.org/x/sys`.
- `golang.org-x-term/LICENSE` and `golang.org-x-term/PATENTS`:
  `golang.org/x/term`.
- `cryptopp/LICENSE.txt`: Crypto++ compilation license, Boost Software
  License 1.0, public-domain declaration, and CRYPTOGAMS notice.
- `gcc/COPYING3` and `gcc/COPYING.RUNTIME`: GNU GPL version 3 and the GCC
  Runtime Library Exception version 3.1 for statically linked GCC runtime
  code.
- `llvm/LICENSE.TXT`, `llvm/libcxx-CREDITS.TXT`, and
  `llvm/compiler-rt-CREDITS.TXT`: Apache License 2.0 with LLVM Exceptions,
  the legacy LLVM license, and relevant upstream credits for LLVM runtimes.
- `mingw-w64/amd64-COPYING.MinGW-w64-runtime.txt` and
  `mingw-w64/arm64-COPYING.MinGW-w64-runtime.txt`: the exact upstream
  runtime notices corresponding to each provider build.

The pinned upstream trees do not publish a standalone top-level `NOTICE`
file for these components. Where upstream publishes `PATENTS`, `CREDITS`,
or a dedicated MinGW runtime notice, that file is included here without
modification.

## Dynamically supplied software

OpenVPN Community `2.7.5-I001` and its OpenSSL `3.6.3` `libcrypto` are not
redistributed in this skill. The setup script downloads and verifies the
official OpenVPN installer, and `lea.dll` dynamically resolves the
architecture-specific `libcrypto` installed by that package. Their source
and installer identities remain recorded in `../assets/manifest.json` and
the native build manifests.

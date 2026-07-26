# Windows ARM64 LEA provider

This experiment builds `lea.dll`, an OpenSSL 3 provider exposing
`LEA-128-CBC`, for the official OpenVPN Community Windows ARM64 package.

## Compatibility target

- OpenVPN Community `2.7.5-I001` ARM64 MSI
- OpenSSL `3.6.3`
- PE machine `IMAGE_FILE_MACHINE_ARM64`
- imported OpenSSL ABI name `libcrypto-3-arm64.dll`
- one public PE export: `OSSL_provider_init`

`versions.env` pins the official MSI, signature, source archives, cross
toolchain, extraction container, and their digests. The native build uses the
same vcpkg baseline pinned by the official OpenVPN `2.7.5-I001` build.

## Reproducible macOS cross-build

Run from the `tools/secuway-native` directory:

```sh
./experiments/windows-arm64/build-macos.sh
```

The script:

1. verifies every downloaded archive by SHA-256;
2. verifies the OpenVPN MSI's detached signature against the exact official
   release-key fingerprint;
3. extracts `libcrypto-3-arm64.dll` from the official MSI without
   redistributing it;
4. generates an import library from that exact DLL;
5. builds Crypto++ statically with ARM assembly disabled;
6. links and strips `dist/lea.dll` and `dist/provider_smoke.exe` with zero PE
   timestamps; and
7. runs `verify-pe.sh`.

The local verifier rejects a non-ARM64 artifact, an export set other than
`OSSL_provider_init`, the wrong OpenSSL DLL import name, or an external
LLVM/GCC runtime dependency. It also confirms that the shared KAT harness is
an ARM64 executable importing the official OpenSSL ABI name.

## Native Windows ARM64 validation

Copy `workflow/windows-arm64.yml` to
`.github/workflows/windows-arm64.yml` in the repository containing
`tools/secuway-native`, then run the workflow on GitHub's
`windows-11-arm` runner.

The workflow builds natively with MSVC and the pinned vcpkg baseline, checks
the official MSI's SHA-256 and Authenticode signature, and runs
`test-on-windows.ps1`. The native test requires all of the following:

- ARM64 PE headers for the provider, smoke test, official `openssl.exe`, and
  official `openvpn.exe`;
- exactly one provider export and the exact official OpenSSL import name;
- provider loading in official OpenSSL `3.6.3`;
- the shared `tests/provider_smoke.c` LEA-CBC encryption and decryption KAT;
- an independent OpenSSL CLI LEA-CBC KAT and decrypt round trip; and
- discovery of `LEA-128-CBC` by official OpenVPN `2.7.5`.

Successful CI uploads `lea.dll`, `provider_smoke.exe`,
`runtime-evidence.txt`, `build-manifest.txt`, and `SHA256SUMS`. A local
cross-build passing PE inspection is not, by itself, a native runtime result;
the workflow log and uploaded manifest are the native evidence.

## Runtime integration boundary

Place `lea.dll` in a dedicated OpenSSL modules directory, point
`OPENSSL_MODULES` at that directory, and load providers `lea` and `default`.
OpenVPN must be invoked with the corresponding provider configuration.

LEA-CBC is not compatible with OpenVPN DCO, so a LEA-CBC tunnel must disable
DCO. Tunnel interoperability still requires a real Secuway endpoint and is a
separate validation gate from provider loading and cipher discovery.

## Distribution boundary

This directory contains no password, OTP, VPN profile, account identifier, or
proprietary Secuway binary. It does not redistribute the OpenVPN MSI or its
DLLs. Release packaging must preserve the applicable notices for OpenSSL,
Crypto++, and this repository's own source license.

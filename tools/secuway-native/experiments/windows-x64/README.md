# Windows x64 LEA provider experiment

This directory builds `lea.dll`, an OpenSSL 3 provider for
`LEA-128-CBC`, for the official OpenVPN Community Windows x64 runtime.

The build downloads and checksum-pins:

- OpenVPN Community 2.7.5-I001 x64 MSI
- OpenSSL 3.6.3 source
- Crypto++ 8.9.0's official GitHub tag archive

The import library is generated from the exact
`libcrypto-3-x64.dll` extracted from the pinned official OpenVPN MSI.
The OpenVPN MSI and its DLLs are build inputs only and are not copied
to `artifacts/`.

Run:

```sh
./experiments/windows-x64/build.sh
```

Static validation checks the PE architecture, the
`OSSL_provider_init` export, the `libcrypto-3-x64.dll` dependency, and
the absence of dynamic MinGW runtime dependencies.

Run the non-tunneling runtime smoke test under Wine:

```sh
./experiments/windows-x64/verify-wine.sh
```

That test uses the official MSI's `openssl.exe`, `openvpn.exe`, and
OpenSSL DLLs in an ephemeral directory. It verifies a published LEA
known-answer vector in CBC mode with a zero IV, checks both encryption
and decryption, and records OpenVPN cipher discovery. It does not claim
a Windows host tunnel validation.

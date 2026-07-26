# Secuway Portable Validation Matrix

Updated: 2026-07-27

Each cell advances independently. A CLI build or provider load is never evidence
that a real VPN tunnel works.

| Target | CLI build | Credential store runtime | LEA/OpenVPN doctor | Live gateway policy | Live login | Live tunnel |
| --- | --- | --- | --- | --- | --- | --- |
| macOS ARM64 | PASS | PASS, Security.framework Keychain | PASS | PASS, ID/PW + app OTP, loginselect 51 | NOT RUN | NOT RUN |
| macOS x86-64 | PASS | COMPILE ONLY | NOT BUILT | NOT RUN | NOT RUN | NOT RUN |
| Linux ARM64 | PASS | PASS, protected 0600 file | NOT BUILT | NOT RUN | NOT RUN | NOT RUN |
| Linux x86-64 | PASS | PASS, protected 0600 file | NOT BUILT | NOT RUN | NOT RUN | NOT RUN |
| Windows ARM64 | PASS, native Windows 11 ARM64 | PASS, user-scoped DPAPI | PASS, native provider/KAT/OpenVPN cipher discovery; full installer and LZO doctor NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Windows x86-64 | PASS, native Windows x64 | PASS, user-scoped DPAPI | PASS, official signed OpenVPN install, LEA/LZO doctor, and KAT | NOT RUN | NOT RUN | NOT RUN |

## Native Windows evidence

- [Portable run 30215976274](https://github.com/mrcha033/skills/actions/runs/30215976274)
  built and ran the CLI on Windows x64 and Windows 11 ARM64. Both runners
  passed PE-architecture checks, a synthetic DPAPI save/load/delete round trip,
  bundled-CLI reproducibility, and the fresh-store status contract.
- [x64 provider/install run 30215559503](https://github.com/mrcha033/skills/actions/runs/30215559503)
  verified the official OpenVPN 2.7.5-I001 MSI hash and Authenticode signature,
  installed it on native Windows, installed the matching LEA provider, passed
  the LEA/LZO doctor and LEA encrypt/decrypt KAT, protected the profile
  directory, started the Interactive Service, and restored the prior service
  state and exact user `PATH` after uninstall.
- [ARM64 provider run 30215976293](https://github.com/mrcha033/skills/actions/runs/30215976293)
  tested both a fresh MSVC build and the distributed ARM64 provider. Each
  passed ARM64 PE/ABI checks, exact OpenSSL 3.6.3 provider loading,
  LEA-128-CBC KAT and round-trip, and OpenVPN 2.7.5-I001 cipher discovery.

The GitHub-hosted x64 install runner was already administrative, so
self-elevation from a non-admin caller and cross-user original-caller
resolution remain untested. ARM64 full install/ACL/service/uninstall, actual
non-admin named-pipe tunnel startup, and every live gateway/login/tunnel gate
also remain untested.

## Gates

- `CLI build`: target-native executable produced with the expected binary
  architecture.
- `Credential store runtime`: synthetic non-secret profile completed a
  save/load/delete round trip on the target OS.
- `LEA/OpenVPN doctor`: the target OpenVPN process loaded the bundled LEA
  provider, listed `LEA-128-CBC`, and reported LZO support.
- `Live gateway policy`: a credential-free probe reached the real HTTPS gateway
  and parsed its current policy.
- `Live login`: one legitimate user enrollment returned a profile which passed
  strict PEM, remote, route, cipher, and directive validation.
- `Live tunnel`: OpenVPN reached `Initialization Sequence Completed`; the
  expected campus route and a designated internal test endpoint were then
  reachable.

## Authentication and secret boundary

- OTP is never bypassed, generated, stored, logged, or accepted as blank.
- The first login on each host requires the user's ID, password, and Google
  Authenticator OTP.
- Only the server-issued tunnel profile is cached. Password and OTP are not
  cached.
- Cached profiles use macOS Keychain, Windows user-scoped DPAPI, or a
  current-user-owned Linux mode-0600 file in a mode-0700 directory.
- OpenVPN DCO is disabled because the gateway requires `LEA-128-CBC` and LZO.
- No Secuway proprietary executable is included in this project.

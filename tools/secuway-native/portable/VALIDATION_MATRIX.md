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
| Windows ARM64 | PASS | COMPILE ONLY, native CI pending | PE/ABI PASS; bundled native runtime CI pending | NOT RUN | NOT RUN | NOT RUN |
| Windows x86-64 | PASS | COMPILE ONLY, native CI pending | PASS with official OpenVPN under Wine; native install CI pending | NOT RUN | NOT RUN | NOT RUN |

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

# Windows validation results

Date: 2026-07-27

## Native GitHub Actions evidence

| Check | Windows x64 | Windows ARM64 |
| --- | --- | --- |
| Native CLI build and runtime | PASS | PASS |
| PE machine field | PASS, `0x8664` | PASS, `0xAA64` |
| Bundled CLI reproducibility | PASS | PASS |
| User-scoped DPAPI round trip | PASS | PASS |
| Fresh-store status contract | PASS | PASS |
| Official OpenVPN MSI hash/signature | PASS, installed MSI | PASS, administratively extracted MSI |
| LEA provider PE/ABI | PASS, bundled provider | PASS, fresh MSVC build and bundled provider |
| OpenSSL provider load | PASS, OpenSSL 3.6.3 | PASS, OpenSSL 3.6.3 |
| LEA encrypt/decrypt KAT | PASS | PASS, both providers |
| OpenVPN LEA cipher discovery | PASS, OpenVPN 2.7.5 | PASS, OpenVPN 2.7.5 |
| LZO doctor | PASS | NOT RUN |
| Secuway support install/ACL/service-state/cleanup | PASS; OpenVPN retained | NOT RUN |

Evidence links:

- [Windows x64/ARM64 portable run 30215976274](https://github.com/mrcha033/skills/actions/runs/30215976274)
- [Windows x64 provider and native install run 30215559503](https://github.com/mrcha033/skills/actions/runs/30215559503)
- [Windows ARM64 provider run 30215976293](https://github.com/mrcha033/skills/actions/runs/30215976293)
- [Windows ARM64 provider artifact](https://github.com/mrcha033/skills/actions/runs/30215976293/artifacts/8635917669)

The portable run executed the Go test suite, DPAPI test, CLI, status boundary,
and PE checks natively on both Windows architectures. The x64 install run
verified the official OpenVPN 2.7.5-I001 MSI hash and Authenticode signature,
installed the architecture-matched LEA provider, passed the LEA/LZO doctor and
KAT, enforced the user profile-directory ACL, confirmed the Interactive
Service was running, and restored its prior state and the exact user `PATH`
during Secuway-support uninstall. The official OpenVPN installation was
retained.

The ARM64 run checked a fresh MSVC-built provider and the distributed provider
independently. For both, it confirmed ARM64 PE files, the sole
`OSSL_provider_init` export, the `libcrypto-3-arm64.dll` import, absence of an
external LLVM/GNU runtime DLL dependency, OpenSSL 3.6.3 provider loading,
LEA-128-CBC encrypt/decrypt KAT and round-trip, and OpenVPN 2.7.5-I001 cipher
discovery. That run did not reject MSVC runtime DLL imports; the subsequent
static-CRT hardening is tracked by the ARM64 workflow rather than attributed
to run 30215976293.

## Evidence still not obtained

The following remain unvalidated and must not be reported as passing:

- UAC self-elevation and original-user SID ACL behavior;
- ARM64 full install, ACL, service, uninstall, and `PATH` restoration;
- actual non-admin Interactive Service named-pipe tunnel startup;
- live Windows gateway-policy discovery;
- real Secuway login or server-issued profile enrollment on Windows;
- a real Windows VPN tunnel and `Initialization Sequence Completed`;
- designated internal endpoint reachability.

The GitHub-hosted x64 runner was already administrative, so it did not exercise
the one-UAC transition from a non-admin outer process or cross-user caller
resolution. The self-hosted tunnel workflow remains manual, environment-gated,
and undispatched. Provider loading, doctor output, and cached enrollment state
are not evidence of a working VPN tunnel.

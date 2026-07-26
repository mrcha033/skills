# Windows validation results

Date: 2026-07-27

## Evidence obtained on macOS ARM64

| Check | Windows x64 | Windows ARM64 | Evidence level |
| --- | --- | --- | --- |
| Portable Go CLI | PASS | PASS | Cross-compiled PE |
| Windows store test binary | PASS | PASS | Cross-compiled PE |
| PE machine field | PASS, `0x8664` | PASS, `0xAA64` | Parsed and confirmed |
| Go tests on the development host | N/A | N/A | PASS on Darwin ARM64 only |
| PowerShell parser | PASS | PASS | PowerShell 7.6 parser, seven unique files |
| PSScriptAnalyzer | PASS | PASS | Errors/warnings, console-output rule excluded |
| GitHub Actions syntax | PASS | PASS | YAML parser and actionlint |

Commands that ran:

```text
go test ./...
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build ./cmd/secuway
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go test -c ./internal/store
CGO_ENABLED=0 GOOS=windows GOARCH=arm64 go build ./cmd/secuway
CGO_ENABLED=0 GOOS=windows GOARCH=arm64 go test -c ./internal/store
```

The generated binaries were identified as:

```text
PE32+ executable (console) x86-64, for MS Windows
PE32+ executable (console) Aarch64, for MS Windows
```

The seven unique PowerShell entry points parsed successfully; the two packaged
installer copies are byte-identical. PSScriptAnalyzer passed after excluding
only `PSAvoidUsingWriteHost`, because these are user-facing console programs.
The four workflow files passed actionlint after allowing the
current official `windows-11-arm` label and the intentionally custom
`secuway-tunnel` label.

## Evidence not yet obtained

The following remain unvalidated and must not be reported as passing:

- DPAPI save/load/delete runtime on Windows x64;
- DPAPI save/load/delete runtime on Windows ARM64;
- official OpenVPN Authenticode and registry checks on an installed target;
- x64 or ARM64 `lea.dll` load by the matching Windows OpenVPN process;
- LZO doctor on Windows;
- UAC self-elevation and original-user SID ACL behavior;
- non-admin Interactive Service named-pipe startup;
- real Secuway login or tunnel;
- designated internal endpoint reachability.

## Runner inventory

- Five concrete local SSH aliases were inspected; none was identifiable as a
  Windows host.
- No local UTM, Parallels, VMware, VHDX, or QCOW2 Windows machine was found.
- The connected `mrcha033/skills` repository has no self-hosted Actions runner.
- GitHub-hosted `windows-latest` and `windows-11-arm` are encoded in the
  repository workflows. Their first native run remains pending the initial
  branch push.

The next evidence-producing action is to place the workflow in an authorized
repository and run both native jobs. Bundle doctor and tunnel validation remain
separate later gates.

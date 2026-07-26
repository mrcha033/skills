# Windows validation path

This directory separates four claims that must not be conflated:

1. a Windows PE binary was produced;
2. the Windows credential backend completed a target-native DPAPI round trip;
3. OpenVPN loaded the matching LEA provider and reported LZO support;
4. a real tunnel came up and the expected internal endpoint was reachable.

No ID, password, OTP, OTP seed, or cached profile is printed or uploaded.

## Portable runtime

Run this on each native Windows target:

```powershell
.\experiments\windows-ci\Test-Windows.ps1 `
  -Phase Portable `
  -ExpectedArch amd64
```

Use `arm64` on Windows ARM64. The test:

- runs the Windows-only `TestDPAPIRoundTrip` test with synthetic data;
- runs the full Go test suite natively;
- builds `secuway.exe`;
- parses the PE header and rejects the wrong architecture;
- executes `version`, fresh `status --json`, and `forget`;
- verifies `windows-dpapi`, `NEEDS_ENROLLMENT`, and
  `secrets_printed=false`.

`windows-portable.yml` runs the same checks on `windows-latest` and
`windows-11-arm`. Copy it into `.github/workflows/` when this source tree is
placed in its release repository.

## Bundle doctor

The expected bundle layout is:

```text
dist/windows-amd64/
  bin/secuway.exe
  libexec/openvpn.exe
  lib/ossl-modules/lea.dll
```

Run:

```powershell
.\experiments\windows-ci\Test-Windows.ps1 `
  -Phase Doctor `
  -ExpectedArch amd64 `
  -BundleRoot .\dist\windows-amd64
```

The doctor phase rejects architecture mismatches for all three PE files, then
requires `LEA-128-CBC` and LZO from the bundled OpenVPN process. A successful
portable phase is not a successful doctor.

## Non-admin runtime

The production Windows path should use OpenVPN's
`OpenVPNServiceInteractive`, not require an elevated PowerShell on every
connection.

Install the current official OpenVPN package for the machine architecture
first. This project intentionally does not download or silently install an MSI.
Use the installer published on the
[OpenVPN Community download page](https://community.openvpn.net/Downloads),
then run the Secuway installer from an ordinary PowerShell:

```powershell
.\experiments\windows-ci\Install-Windows.ps1 `
  -Action Install `
  -LeaProviderPath .\dist\windows-amd64\lib\ossl-modules\lea.dll
```

The script asks for UAC once and preserves the original caller SID across
elevation. It then:

1. validates the official OpenVPN registry, executable location,
   Authenticode signer, native PE architecture, `config_dir`, and Interactive
   Service;
2. rejects a non-DLL or wrong-architecture `lea.dll`;
3. installs it as `<OpenVPN>\ssl\modules\lea.dll`, retaining a different
   pre-existing provider for rollback;
4. provisions
   `<config_dir>\mrcha-secuway\<original-user-SID>` with inheritance disabled
   and only SYSTEM/Administrators Full Control plus that SID Modify;
5. runs the real OpenVPN LEA/LZO doctor and starts
   `OpenVPNServiceInteractive`.

If installation fails, the script restores the previous provider and directory
ACL. It records no credentials. Check machine-readable state with:

```powershell
.\experiments\windows-ci\Install-Windows.ps1 -Action Status
```

Remove only the Secuway integration with:

```powershell
.\experiments\windows-ci\Install-Windows.ps1 -Action Uninstall
```

Uninstall leaves official OpenVPN installed. A profile directory created by
this installer, including cached tunnel profiles inside it, is deleted and is
not recoverable; a directory that predated installation is retained with its
original ACL restored.

The profile emitted for this service path must contain:

   ```text
   providers lea default
   disable-dco
   windows-driver tap-windows6
   ```

Runtime then connects to `\\.\pipe\openvpn\service` as the ordinary user and
sends the official three-string UTF-16 startup message in one write:

```text
working-directory NUL openvpn-options NUL stdin NUL
```

Only `--config`, `--log`, and `--verb` are sent as startup options. The LEA,
DCO, and driver directives belong inside the validated profile because the
Interactive Service command-line allowlist does not include them for ordinary
users.

`Invoke-InteractiveServiceSmoke.ps1` implements this protocol for validation.
Its `-Preflight` mode checks the service and provider without a profile:

```powershell
.\experiments\windows-ci\Invoke-InteractiveServiceSmoke.ps1 `
  -Mode Preflight `
  -RequireNonAdmin
```

Tunnel validation uses a previously enrolled profile by local path. It never
accepts ID, password, or OTP parameters and never prints the profile:

```powershell
.\experiments\windows-ci\Invoke-InteractiveServiceSmoke.ps1 `
  -Mode Tunnel `
  -ConfigPath C:\ProgramData\MrchaSkills\Secuway\profiles\current.ovpn `
  -ProbeHost internal.example `
  -ProbePort 22 `
  -RequireNonAdmin
```

The profile is required to live under the service's configured `config_dir`.
The test waits for `Initialization Sequence Completed`, checks the optional
internal endpoint, terminates the test tunnel, and deletes its log by default.

`windows-tunnel-self-hosted.yml` is deliberately limited to an approved
self-hosted runner labeled `secuway-tunnel`. It passes only local paths and an
internal probe target through host environment variables:

- `SECUWAY_TUNNEL_CONFIG`
- `SECUWAY_TUNNEL_PROBE_HOST`
- `SECUWAY_TUNNEL_PROBE_PORT`

Do not run the tunnel job on a general GitHub-hosted runner and do not upload
the profile or OpenVPN log as an artifact.

## Validation state

- macOS cross-build can prove only Windows compilation and PE architecture.
- `windows-latest` can prove Windows x64 runtime and DPAPI.
- `windows-11-arm` can prove Windows ARM64 runtime and DPAPI.
- Doctor requires a matching `openvpn.exe` and `lea.dll`.
- Tunnel validation requires a real, target-native Windows host with the
  one-time enrollment and Interactive Service installation already completed.

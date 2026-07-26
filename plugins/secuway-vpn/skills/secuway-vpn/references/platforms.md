# Platform and validation reference

## Status meanings

- `NOT_INSTALLED`: no usable Secuway CLI was found.
- `NOT_READY`: the CLI exists, but OpenVPN, the LEA provider, or another local
  runtime check failed.
- `NEEDS_ENROLLMENT`: the local runtime passed, but no server-issued profile is
  cached for this gateway.
- `CACHED`: a locally protected profile can be decrypted. This is not a fresh
  server check and does not prove a tunnel is up.
- `READY` from `secuway_status.py`: both local runtime checks and local cache
  checks passed. Verify the live tunnel separately.

Never describe a local cache result as a current server login.

## Windows amd64 and arm64

The skill bundles architecture-matched `secuway.exe`, `lea.dll`, and a provider
KAT executable. `Setup-Windows.ps1` verifies their sizes and SHA-256 hashes.
When official OpenVPN Community is absent, it downloads the pinned 2.7.5-I001
MSI for the native architecture and verifies its SHA-256 and Authenticode
signature before requesting one UAC approval.

Install:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "<skill>\scripts\Setup-Windows.ps1" -Action Install
```

Read-only local installation status:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "<skill>\scripts\Setup-Windows.ps1" -Action Status
```

Removal deletes the local cached tunnel profile and cannot recover it. Run only
after the user explicitly asks:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "<skill>\scripts\Setup-Windows.ps1" -Action Uninstall
```

The setup preserves the original caller SID across UAC. It installs the provider
under OpenVPN's registered `ssl\modules`, creates
`config_dir\mrcha-secuway\<SID>` with inheritance disabled, and grants only
SYSTEM and Administrators full control plus that SID modify access. Normal
connections then use `OpenVPNServiceInteractive`; they do not prompt for
administrator approval on every connection.

`secuway login --output` and `secuway connect --config` are intentionally
refused on Windows. A profile contains private key material, a POSIX mode value
does not establish a private Windows DACL, and an arbitrary config must not
cross the Interactive Service privilege boundary. Use DPAPI-backed cached
enrollment and `secuway connect`.

## macOS

Use a native `secuway` installation whose `doctor` reports the matching
OpenVPN, LEA provider, and LZO support. Successful profiles are stored in the
login Keychain. OpenVPN route and tunnel creation still requires `sudo`.

The Windows assets in this skill are not macOS binaries. If no macOS-native
runtime is installed, report `NOT_INSTALLED`; do not attempt to execute Windows
assets through Wine or Rosetta.

## Linux

The portable CLI supports amd64 and arm64 Linux. It protects the server-issued
profile in a current-user-owned directory with mode `0700` and a regular file
with mode `0600`; it rejects a file owned by another user or with broader
permissions. OpenVPN tunnel creation requires `sudo`.

A Linux host must have its own OpenVPN/OpenSSL LEA runtime. If `doctor` fails,
report the exact missing component. Do not route through a different Mac as an
implicit boundary host.

## Enrollment and OTP

Run enrollment only in a terminal directly visible to the user:

```text
secuway connect
```

The CLI obtains the gateway's current policy and prompts locally for only the
required factors. Never ask the user to paste an ID, password, app OTP, OTP
seed, profile, certificate, or private key into chat. Never put those values in
arguments, environment variables, CI inputs, logs, or files.

One-time enrollment means reusing a still-valid, server-issued certificate and
profile. It does not mean bypassing OTP. An app OTP cannot be replaced or
bypassed. If the gateway requires one and the user cannot produce it, stop at
`NEEDS_ENROLLMENT`.

The cache stores neither password nor OTP. `forget` removes the local profile;
the next connection must perform server-approved enrollment again.

## Live tunnel proof

Treat a connection as live only after all applicable checks pass:

1. OpenVPN reports `Initialization Sequence Completed`.
2. The expected tunnel adapter/interface exists.
3. The route to the protected endpoint uses that interface.
4. A bounded probe to the intended internal endpoint succeeds.

Do not infer success from a process ID, cached profile, service status, or a
local `doctor` result alone.

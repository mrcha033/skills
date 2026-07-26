# Secuway Portable

Portable, independent SecuwaySSL-compatible enrollment and OpenVPN launcher for
macOS, Linux, and Windows on amd64 and arm64. It does not invoke or redistribute
vendor client binaries.

The CLI performs the gateway's supported authentication flow, validates the
returned profile, stores only that server-issued profile, and reuses it until
the server expires or revokes it. Passwords, OTP values, and OTP seeds are
never cached.

## Runtime model

| Platform | Profile store | Tunnel elevation |
| --- | --- | --- |
| macOS | Login Keychain | `sudo` for OpenVPN |
| Linux | User-owned mode-0600 file | `sudo` for OpenVPN |
| Windows | Current-user DPAPI | One-time UAC setup, then OpenVPN Interactive Service |

Windows setup installs the LEA provider beside an official OpenVPN Community
installation and provisions a per-user profile directory under OpenVPN's
registered `config_dir`. The connection process then runs as the caller; only
route, adapter, and DNS operations cross the official privileged service.

## CLI

```text
secuway doctor
secuway probe
secuway status --json
secuway login
secuway connect
secuway forget
```

Run enrollment in an interactive terminal. Do not place an ID, password, OTP,
profile, or private key in command-line arguments, environment variables,
automation inputs, logs, or chat messages.

## Build and test

The portable CLI:

```sh
cd portable
go test ./...
go test -race ./...
./scripts/build-cli.sh
```

The reproducible Windows x64 provider build and official OpenVPN runtime check:

```sh
./experiments/windows-x64/build.sh
./experiments/windows-x64/verify-wine.sh
```

The Windows ARM64 provider can be cross-built from macOS:

```sh
./experiments/windows-arm64/build-macos.sh
```

Native Windows x64/ARM64 DPAPI tests and provider checks run in the repository
workflows. A real tunnel test is deliberately separate because it requires a
user-owned, pre-enrolled profile and a host with campus reachability.

## Security boundary

- Authentication is never bypassed. A first successful server-approved login
  is required before profile reuse.
- Gateway redirects are restricted to the original HTTPS origin.
- Returned PEM blocks, remotes, routes, cipher, and optional directives are
  allow-listed before an OpenVPN configuration is created.
- Windows provider installation verifies the official OpenVPN signatures,
  native architecture, provider hash, protected ACL, and Interactive Service.
- `forget` deletes the locally cached server profile. It does not revoke the
  certificate at the gateway.
- No proprietary Secuway binaries, credentials, profiles, or private keys are
  part of this source tree or its CI artifacts.

See [THIRD_PARTY.md](THIRD_PARTY.md) for pinned upstream components.

# Deterministic systemd supervision contract

`systemd_units.py generate` creates user-level service and timer files without
changing the user manager. This is a safety property of the command, not a
reason for an agent to stop early. When the user explicitly requests actual
installation or automation, generation is an intermediate step: review and
verify the exact generated units, then install, enable, start, and inspect only
the requested scope.

## Bundle

The input is one exact `qta-systemd-bundle/v2` JSON object. It binds:

- the exact Python executable and installed technical/trader skill roots;
- a user-owned, regular, non-symlink mode-0600 environment file;
- one absolute runtime directory containing all mutable jobs, plans, state,
  venue maps, and status files;
- unique static EOD/account-snapshot/entry jobs or daily
  prepare/snapshot/entry jobs for KR and US.

Daily preparation schedules are 07:00 Asia/Seoul and 08:00
America/New_York on weekdays. Entry services start at 08:59 Asia/Seoul and
09:29 America/New_York so the
runner can complete its 30-second token warm-up before the exchange open.
Account snapshot services run at 08:50 Asia/Seoul and 09:20
America/New_York, use KIS live credentials in shadow mode, and must complete
before their market's entry service.
Entry timers are never persistent: a sleeping or offline host must not replay a
missed opening session. Account snapshot timers are also never persistent.
Daily preparation and legacy EOD timers are persistent; late daily preparation
still fails its pre-open deadline rather than replaying an entry. Entry and
snapshot timers are never persistent.

For recurring automation, use `daily-prepare`, `daily-snapshot`, and
`daily-entry` jobs whose `input_path` is the stable
`qta-daily-shadow-config/v2` file described in
`daily-pipeline-contract.md`. The daily runner calculates the session date and
atomically publishes current descriptors. A timer bound to a date-stamped job
file remains session-specific and must not be reported as future-date
automation. Never reuse a prior session's plan, calendar, state directory, or
output path on a later date.

The generator accepts only KIS paper/paper or KIS live/shadow entry pairs.
It refuses live mode. The generated service executes Python directly without a
shell, removes `PYTHONPATH` and `PYTHONHOME` before the wrapper starts and
again before the worker exec, disables bytecode writes, applies systemd
filesystem/process hardening, and grants writes only to the bound runtime
directory.

## Single writer

Every generated execution acquires a nonblocking, non-symlink regular-file
market lock below a non-symlink `runtime_directory/locks`. Daily preparation
updates one four-exchange universe and therefore acquires both locks in
byte-stable order. Snapshot and entry acquire only their market lock. Failure
to acquire a lock is `BLOCKED`, not a retry or a second writer. The wrapper
sets every opened lock file to mode `0600`, including a regular lock file that
predates the current bundle, and verifies the resulting mode before acquiring
it.

Generate into a new or empty output directory so stale service/timer files
cannot survive from an older bundle. The generated `systemd-bundle.json` is a
normalized, hashed, exact `qta-systemd-bundle/v2` object that the `execute`
subcommand must be able to read without projection or manual editing.
Derived `schedule` and `persistent` values belong only in generated timer
files; they are not serialized as job input fields.

Unit files and their SHA-256 values are recorded in
`generation-status.json` with:

```text
activation_performed=false
live_enabled=false
api_mutation_count=0
```

Run `systemd-analyze --user verify` against the generated files on the target
Linux host before copying them to `~/.config/systemd/user/`. Resolve the
intended filenames explicitly; do not install a wildcard that could include a
stale or unreviewed unit. After an explicitly requested activation, run
`systemctl --user daemon-reload`, enable/start only the approved timers or
services, and verify `is-enabled`, `is-active`, `list-timers`, and the service
journal. Entry or snapshot activation must not occur until the daily config,
credentials, runtime ownership, and installed skill roots are valid. The daily
producer itself creates the date-bound calendar, plan, and state path before
entry; a missing stage descriptor is `BLOCKED`.

# Deterministic systemd supervision contract

`systemd_units.py generate` creates user-level service and timer files without
changing the user manager. This is a safety property of the command, not a
reason for an agent to stop early. When the user explicitly requests actual
installation or automation, generation is an intermediate step: review and
verify the exact generated units, then install, enable, start, and inspect only
the requested scope.

## Bundle

The input is one exact `qta-systemd-bundle/v1` JSON object. It binds:

- the exact Python executable and installed technical/trader skill roots;
- a user-owned, regular, non-symlink mode-0600 environment file;
- one absolute runtime directory containing all mutable jobs, plans, arms,
  state, venue maps, and receipts;
- unique EOD, account-snapshot, or entry jobs for KR and US.

EOD schedules are 07:00 Asia/Seoul and 18:00 America/New_York on weekdays.
Entry services start at 08:59 Asia/Seoul and 09:29 America/New_York so the
runner can complete its 30-second token warm-up before the exchange open.
Account snapshot services run at 08:50 Asia/Seoul and 09:20
America/New_York, use KIS live credentials in shadow mode, and must complete
before their market's entry service.
Entry timers are never persistent: a sleeping or offline host must not replay a
missed opening session. Account snapshot timers are also never persistent.
EOD timers are persistent and their jobs still require an exact
completed-session cutoff.

Recurring EOD automation requires a deterministic daily producer that
atomically refreshes a stable job-input path. A timer bound to a date-stamped
job file is session-specific and must not be reported as future-date
automation. Account snapshot and entry inputs are session-bound; never reuse a
prior session's plan, arm, calendar, state directory, or output path on a later
date.

The generator accepts only KIS paper/paper or KIS live/shadow entry pairs.
It refuses live mode. The generated service executes Python directly without a
shell, removes `PYTHONPATH` and `PYTHONHOME` before the wrapper starts and
again before the worker exec, disables bytecode writes, applies systemd
filesystem/process hardening, and grants writes only to the bound runtime
directory.

## Single writer

Every generated execution acquires a nonblocking, non-symlink regular-file
market lock below a non-symlink `runtime_directory/locks`. KR EOD and entry
work cannot overlap each other; the same applies to US. Failure to acquire the
lock is `BLOCKED`, not a retry or a second writer.

Generate into a new or empty output directory so stale service/timer files
cannot survive from an older bundle. The generated `systemd-bundle.json` is a
normalized, hashed, exact `qta-systemd-bundle/v1` object that the `execute`
subcommand must be able to read without projection or manual editing.
Derived `schedule` and `persistent` values belong only in generated timer
files; they are not serialized as job input fields.

Unit files and their SHA-256 values are recorded in
`generation-receipt.json` with:

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
journal. Entry or snapshot activation must not occur until the daily plan/arm
producer, calendar snapshots, credentials, runtime ownership, and provenance
approval are all bound.

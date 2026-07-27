# Deterministic systemd supervision contract

`systemd_units.py` generates user-level service and timer files; it never
copies them into a systemd search path, reloads the manager, enables a timer,
or starts a service. Activation is an explicit operator action after reviewing
the generated receipt and paths.

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

The generator accepts only KIS paper/paper or KIS live/shadow entry pairs.
It refuses live mode. The generated service executes Python directly without a
shell, clears `PYTHONPATH` and `PYTHONHOME`, disables bytecode writes, applies
systemd filesystem/process hardening, and grants writes only to the bound
runtime directory.

## Single writer

Every generated execution acquires a nonblocking market lock below
`runtime_directory/locks`. KR EOD and entry work cannot overlap each other;
the same applies to US. Failure to acquire the lock is `BLOCKED`, not a retry
or a second writer.

The generated `systemd-bundle.json` is normalized and hashed. Unit files and
their SHA-256 values are recorded in `generation-receipt.json` with:

```text
activation_performed=false
live_enabled=false
api_mutation_count=0
```

Run `systemd-analyze --user verify` against the generated files on the target
Linux host before copying them to `~/.config/systemd/user/`. Enabling timers is
outside the skill and must not occur until the daily plan/arm producer,
calendar snapshots, credentials, runtime ownership, and provenance approval
are all bound.

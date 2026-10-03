# Command line

## Commands

| Command | What it does |
|---|---|
| `sherpa discover …` | Scan AWS and/or GitHub. Saves a snapshot to the database and writes `snapshot_<id>.json` and `report_<id>.md` to `--output` (default `./inventory`) |
| `sherpa snapshots list` | List stored snapshots, oldest first: sources, counts, coverage gaps, skipped scopes |
| `sherpa snapshots show <id>` | Summary of one snapshot. `<id>` may be a unique prefix. `--json` prints the full snapshot |

For AWS options (accounts file, regions, roles, ExternalId), see [aws-access.md](aws-access.md).

## Where data lives

Snapshots accumulate in a local SQLite database, so you can compare runs over a deal's lifetime:

- default: `./.sherpa/sherpa.db`, created on first use (`.sherpa/` is gitignored);
- override with `--db <path>` or the `SHERPA_DB` environment variable;
- `--db :memory:` keeps nothing between runs.

Snapshots are **append-only**: a new run never changes an earlier one. Saving and reloading a snapshot is lossless, down to the byte.

## Upgrades

The database has a schema version. When a newer Sherpa opens an older database, it:

1. copies the file to `<name>.bak-v<old version>`;
2. migrates it in a single transaction (all or nothing);
3. prints `Upgraded database … from schema vN to vM`.

A database written by a **newer** Sherpa is refused, with a message asking you to upgrade. Nothing is changed.

Upgrading from schema v1, the format before October 2026, also **restores cross-plane dependency edges**. v1 stored them but couldn't load them back.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Configuration error or failure: invalid config, unknown or ambiguous snapshot ID, database from a newer Sherpa |
| `2` | `discover` finished and saved the snapshot, but one or more scopes (e.g. AWS accounts) were **skipped** because their identity couldn't be confirmed. Treat as a failure in scripts and CI |

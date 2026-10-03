"""Schema versioning and migrations for the SQLite inventory store.

Versions:
  1 — the original schema (no version table). Dependency edges were stored without the
      resource they belong to, so cross-plane edges were lost on reload; coverage gaps,
      errors and scan identities were not stored.
  2 — adds `schema_version`, `dependencies.owner_id` (backfilled), and the gap, error and
      identity columns on `scan_runs`.

Opening an older database migrates it in one transaction, after copying the file to
`<name>.bak-v<old>`. Opening a newer one is refused.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa

from .schema import metadata, schema_version

CURRENT_SCHEMA_VERSION = 2


class SchemaVersionError(RuntimeError):
    """The database was written by a newer Sherpa than this one."""


def _v1_to_v2(conn: sa.Connection) -> None:
    conn.exec_driver_sql("ALTER TABLE dependencies ADD COLUMN owner_id VARCHAR NOT NULL DEFAULT ''")
    # Same rule the cross-plane linker uses: an edge belongs to its source when the source is
    # a resource (cloud edges), otherwise to its target (repo/pipeline → resource edges).
    conn.exec_driver_sql(
        """
        UPDATE dependencies SET owner_id = CASE
            WHEN EXISTS (
                SELECT 1 FROM resources r
                WHERE r.id = dependencies.source_id AND r.snapshot_id = dependencies.snapshot_id
            ) THEN source_id
            ELSE target_id
        END
        """
    )
    for column in ("coverage_gaps_json", "errors_json", "scan_identities_json"):
        conn.exec_driver_sql(
            f"ALTER TABLE scan_runs ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
        )


# from_version -> migration to from_version + 1
MIGRATIONS: dict[int, Callable[[sa.Connection], None]] = {1: _v1_to_v2}


def detect_version(conn: sa.Connection) -> int | None:
    """Schema version of an existing database, or None for an empty one."""
    tables = set(sa.inspect(conn).get_table_names())
    if "schema_version" in tables:
        return int(conn.execute(sa.select(schema_version.c.version)).scalar_one())
    if "scan_runs" in tables:
        return 1
    return None


def ensure_schema(engine: sa.Engine, db_file: Path | None) -> int:
    """Create, verify or migrate the schema. Returns the version found before any change."""
    with engine.connect() as conn:
        found = detect_version(conn)

    if found is None:
        with engine.begin() as conn:
            metadata.create_all(conn)
            conn.execute(schema_version.insert().values(version=CURRENT_SCHEMA_VERSION))
        return CURRENT_SCHEMA_VERSION

    if found > CURRENT_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"This database uses schema v{found}, written by a newer Sherpa; this version "
            f"supports up to v{CURRENT_SCHEMA_VERSION}. Upgrade Sherpa to open it."
        )

    if found < CURRENT_SCHEMA_VERSION:
        if db_file is not None and db_file.exists():
            shutil.copy2(db_file, db_file.with_name(f"{db_file.name}.bak-v{found}"))
        with engine.begin() as conn:  # all steps or none
            for version in range(found, CURRENT_SCHEMA_VERSION):
                MIGRATIONS[version](conn)
            if found == 1:
                schema_version.create(conn)
                conn.execute(schema_version.insert().values(version=CURRENT_SCHEMA_VERSION))
            else:
                conn.execute(schema_version.update().values(version=CURRENT_SCHEMA_VERSION))
    return found

"""Database schema (current version). Changes need a migration in migrations.py."""

import sqlalchemy as sa

metadata = sa.MetaData()

schema_version = sa.Table(
    "schema_version",
    metadata,
    sa.Column("version", sa.Integer, nullable=False),
)

scan_runs = sa.Table(
    "scan_runs",
    metadata,
    sa.Column("snapshot_id", sa.String, primary_key=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("config_json", sa.Text, nullable=False),
    sa.Column("coverage_gaps_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("errors_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("scan_identities_json", sa.Text, nullable=False, server_default="[]"),
)

resources = sa.Table(
    "resources",
    metadata,
    sa.Column("id", sa.String, nullable=False),
    sa.Column("snapshot_id", sa.String, sa.ForeignKey("scan_runs.snapshot_id"), nullable=False),
    sa.Column("resource_type", sa.String, nullable=False),
    sa.Column("region", sa.String, nullable=False),
    sa.Column("account_id", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=False, default=""),
    sa.Column("tags_json", sa.Text, nullable=False, default="{}"),
    sa.Column("metadata_json", sa.Text, nullable=False, default="{}"),
    sa.PrimaryKeyConstraint("id", "snapshot_id"),
)

dependencies = sa.Table(
    "dependencies",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("snapshot_id", sa.String, sa.ForeignKey("scan_runs.snapshot_id"), nullable=False),
    # The resource this edge is attached to: its source for cloud edges, its target for
    # cross-plane edges (whose source is a repository or pipeline, not a resource).
    sa.Column("owner_id", sa.String, nullable=False, server_default=""),
    sa.Column("source_id", sa.String, nullable=False),
    sa.Column("target_id", sa.String, nullable=False),
    sa.Column("dependency_type", sa.String, nullable=False),
    sa.Column("plane", sa.String, nullable=False),
    sa.Column("metadata_json", sa.Text, nullable=False, default="{}"),
)

repositories = sa.Table(
    "repositories",
    metadata,
    sa.Column("id", sa.String, nullable=False),
    sa.Column("snapshot_id", sa.String, sa.ForeignKey("scan_runs.snapshot_id"), nullable=False),
    sa.Column("url", sa.String, nullable=False),
    sa.Column("iac_type", sa.String, nullable=False),
    sa.Column("declared_resource_ids_json", sa.Text, nullable=False, default="[]"),
    sa.Column("package_deps_json", sa.Text, nullable=False, default="[]"),
    sa.Column("has_dockerfile", sa.Boolean, nullable=False, default=False),
    sa.Column("has_docker_compose", sa.Boolean, nullable=False, default=False),
    sa.Column("default_branch", sa.String, nullable=False, default="main"),
    sa.Column("metadata_json", sa.Text, nullable=False, default="{}"),
    sa.PrimaryKeyConstraint("id", "snapshot_id"),
)

pipelines = sa.Table(
    "pipelines",
    metadata,
    sa.Column("id", sa.String, nullable=False),
    sa.Column("snapshot_id", sa.String, sa.ForeignKey("scan_runs.snapshot_id"), nullable=False),
    sa.Column("pipeline_type", sa.String, nullable=False),
    sa.Column("repo_id", sa.String, nullable=False),
    sa.Column("stages_json", sa.Text, nullable=False, default="[]"),
    sa.Column("deploys_to_resource_ids_json", sa.Text, nullable=False, default="[]"),
    sa.Column("deploys_to_accounts_json", sa.Text, nullable=False, default="[]"),
    sa.PrimaryKeyConstraint("id", "snapshot_id"),
)

workloads = sa.Table(
    "workloads",
    metadata,
    sa.Column("id", sa.String, nullable=False),
    sa.Column("snapshot_id", sa.String, sa.ForeignKey("scan_runs.snapshot_id"), nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("resource_ids_json", sa.Text, nullable=False, default="[]"),
    sa.Column("repo_ids_json", sa.Text, nullable=False, default="[]"),
    sa.Column("pipeline_ids_json", sa.Text, nullable=False, default="[]"),
    sa.Column("inferred_from", sa.String, nullable=False, default=""),
    sa.Column("migration_path", sa.String, nullable=False),
    sa.Column("metadata_json", sa.Text, nullable=False, default="{}"),
    sa.PrimaryKeyConstraint("id", "snapshot_id"),
)

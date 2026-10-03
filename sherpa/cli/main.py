from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from sherpa.core.models import NamingConvention, ScanConfig
from sherpa.core.models.accounts import AwsAccountsFile
from sherpa.core.store import CURRENT_SCHEMA_VERSION, InventoryStore, SchemaVersionError
from sherpa.orchestrator import run_discovery

console = Console()

# Exit codes: 0 = success, 1 = config error or failure (e.g. unknown snapshot, database from a
# newer Sherpa), 2 = scan completed but one or more scopes (e.g. AWS accounts) were skipped
# because their identity could not be confirmed. See docs/cli.md.
EXIT_INCOMPLETE_SCAN = 2

DEFAULT_DB = "./.sherpa/sherpa.db"
_db_option = click.option(
    "--db",
    "db_path",
    default=DEFAULT_DB,
    envvar="SHERPA_DB",
    show_default=True,
    help="SQLite inventory database (snapshots accumulate here). ':memory:' keeps nothing.",
)


def _open_store(db_path: str) -> InventoryStore:
    try:
        store = InventoryStore(db_path)
    except SchemaVersionError as exc:
        console.print(f"[red]Database error:[/red] {exc}")
        raise SystemExit(1) from exc
    if store.migrated_from < CURRENT_SCHEMA_VERSION:
        console.print(
            f"[yellow]Upgraded database[/yellow] {db_path} from schema "
            f"v{store.migrated_from} to v{CURRENT_SCHEMA_VERSION} "
            f"(backup: {db_path}.bak-v{store.migrated_from})"
        )
    return store


@click.group()
def cli() -> None:
    """Sherpa — M&A cloud migration discovery tool."""


@cli.command()
@click.option("--aws-account", "aws_accounts", multiple=True, help="AWS account ID(s) to scan.")
@click.option(
    "--accounts-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="YAML file listing accounts with optional per-account regions and roles.",
)
@click.option(
    "--regions",
    default="us-east-1",
    show_default=True,
    help="Comma-separated default AWS regions (overrides the accounts file's default_regions).",
)
@click.option(
    "--role-name",
    default=None,
    help="Role assumed in every account (arn:aws:iam::<account>:role/<name>). "
    "With several accounts the default is SherpaReadOnly.",
)
@click.option(
    "--external-id",
    envvar="SHERPA_AWS_EXTERNAL_ID",
    default=None,
    help="ExternalId required by the accounts' role trust policies.",
)
@click.option(
    "--assume-role",
    "assume_role_arn",
    default=None,
    help="Exact role ARN to assume. Single account only; use --role-name for several.",
)
@click.option("--github-org", default=None, help="GitHub org to scan for repos and pipelines.")
@click.option(
    "--github-token",
    envvar="GITHUB_TOKEN",
    default=None,
    help="GitHub token (prefer the GITHUB_TOKEN env var). Used in memory only; never stored.",
)
@click.option(
    "--output",
    "output_dir",
    default="./inventory",
    show_default=True,
    type=click.Path(),
    help="Directory to write snapshot JSON and report.",
)
@_db_option
@click.option(
    "--naming-convention",
    "naming_convention_file",
    default=None,
    type=click.Path(exists=True),
    help="Path to a YAML or JSON file describing the acquired company's naming conventions.",
)
def discover(
    aws_accounts: tuple[str, ...],
    accounts_file: str | None,
    regions: str,
    role_name: str | None,
    external_id: str | None,
    assume_role_arn: str | None,
    github_org: str | None,
    github_token: str | None,
    output_dir: str,
    db_path: str,
    naming_convention_file: str | None,
) -> None:
    """Run a full discovery scan and produce an inventory snapshot."""
    aws_region_list = [r.strip() for r in regions.split(",") if r.strip()]
    account_settings = {}
    if accounts_file:
        try:
            accounts_doc = AwsAccountsFile.from_file(accounts_file)
        except ValueError as exc:
            console.print(f"[red]Accounts file error:[/red] {exc}")
            raise SystemExit(1) from exc
        account_settings = accounts_doc.account_settings()
        regions_given = (
            click.get_current_context().get_parameter_source("regions")
            == click.core.ParameterSource.COMMANDLINE
        )
        if accounts_doc.default_regions and not regions_given:
            aws_region_list = accounts_doc.default_regions
        role_name = role_name or accounts_doc.role_name
        external_id = external_id or accounts_doc.external_id

    naming = (
        NamingConvention.from_file(naming_convention_file)
        if naming_convention_file
        else NamingConvention()
    )

    try:
        config = ScanConfig(
            aws_accounts=list(aws_accounts),
            aws_regions=aws_region_list,
            assume_role_arn=assume_role_arn,
            aws_role_name=role_name,
            aws_external_id=external_id,
            aws_account_settings=account_settings,
            github_org=github_org,
            github_token=github_token,
            naming_convention=naming,
        )
    except ValueError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise SystemExit(1) from exc

    store = _open_store(db_path)
    out_path = Path(output_dir)

    console.print("[bold cyan]Sherpa[/bold cyan] starting discovery…")
    _print_aws_plan(config)
    console.print(f"  GitHub org   : {github_org or '(none)'}")
    if naming_convention_file:
        console.print(f"  Naming conv  : {naming_convention_file}")
    else:
        console.print("  Naming conv  : (default)")

    try:
        snapshot = asyncio.run(run_discovery(config, store, out_path))
    except ValueError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise SystemExit(1) from exc
    except Exception as exc:
        console.print(f"[red]Discovery failed:[/red] {exc}")
        raise SystemExit(1) from exc

    _print_summary(snapshot)
    console.print(
        f"\n[green]Snapshot saved:[/green] {out_path}/snapshot_{snapshot.snapshot_id}.json"
    )
    console.print(f"[green]Report saved:[/green]   {out_path}/report_{snapshot.snapshot_id}.md")

    if snapshot.unverified_scopes:
        console.print(
            f"\n[red]Incomplete scan:[/red] {len(snapshot.unverified_scopes)} scope(s) skipped "
            f"because their identity could not be confirmed: {', '.join(snapshot.unverified_scopes)}"
        )
        raise SystemExit(EXIT_INCOMPLETE_SCAN)


@cli.group()
def snapshots() -> None:
    """Browse the inventory snapshots stored in the database."""


@snapshots.command("list")
@_db_option
def snapshots_list(db_path: str) -> None:
    """List snapshots, oldest first."""
    rows = _open_store(db_path).snapshot_summaries()
    if not rows:
        console.print(f"No snapshots in {db_path}. Run `sherpa discover` first.")
        return
    table = Table(title=f"Snapshots in {db_path}", show_header=True)
    for column, justify in (
        ("Snapshot", "left"),
        ("Started (UTC)", "left"),
        ("Sources", "left"),
        ("Resources", "right"),
        ("Repos", "right"),
        ("Pipelines", "right"),
        ("Workloads", "right"),
        ("Gaps", "right"),
        ("Skipped", "right"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]
    for r in rows:
        table.add_row(
            r["snapshot_id"],
            r["started_at"].strftime("%Y-%m-%d %H:%M:%S"),
            _sources(r["aws_accounts"], r["github_org"]),
            str(r["resources"]),
            str(r["repositories"]),
            str(r["pipelines"]),
            str(r["workloads"]),
            str(r["coverage_gaps"]),
            str(r["unverified_scopes"]),
        )
    console.print(table)


@snapshots.command("show")
@click.argument("snapshot_id")
@_db_option
@click.option("--json", "as_json", is_flag=True, help="Print the full snapshot as JSON.")
def snapshots_show(snapshot_id: str, db_path: str, as_json: bool) -> None:
    """Show one snapshot. SNAPSHOT_ID may be a unique prefix."""
    store = _open_store(db_path)
    matches = store.resolve_snapshot_id(snapshot_id)
    if len(matches) != 1:
        problem = "No snapshot matches" if not matches else "Ambiguous snapshot ID"
        hint = "" if not matches else f": {', '.join(matches)}"
        console.print(f"[red]{problem}[/red] '{snapshot_id}'{hint}")
        raise SystemExit(1)
    snapshot = store.load_snapshot(matches[0])
    assert snapshot is not None
    if as_json:
        click.echo(snapshot.to_canonical_json(), nl=False)
        return
    console.print(f"[bold]Snapshot[/bold] {snapshot.snapshot_id}")
    console.print(f"  Started   : {snapshot.started_at.isoformat()}")
    completed = snapshot.completed_at.isoformat() if snapshot.completed_at else "not completed"
    console.print(f"  Completed : {completed}")
    console.print(
        f"  Sources   : {_sources(snapshot.config.aws_accounts, snapshot.config.github_org)}"
    )
    _print_summary(snapshot)
    for identity in snapshot.scan_identities:
        mark = "[green]✓[/green]" if identity.verified else "[red]✗[/red]"
        who = identity.principal if identity.verified else identity.detail
        console.print(f"  {mark} {identity.scope}: {who}")


def _sources(aws_accounts: list[str], github_org: str | None) -> str:
    parts = []
    if aws_accounts:
        parts.append(f"AWS ×{len(aws_accounts)}")
    if github_org:
        parts.append(f"GitHub {github_org}")
    return ", ".join(parts) or "-"


def _print_aws_plan(config: ScanConfig) -> None:
    """Show exactly what will be scanned with which credentials, before any API call."""
    targets = config.aws_targets()
    if not targets:
        console.print("  AWS accounts : (none)")
        return
    table = Table(title="AWS scan plan", show_header=True)
    table.add_column("Account", style="cyan")
    table.add_column("Name")
    table.add_column("Regions")
    table.add_column("Credentials")
    for t in targets:
        creds = t.role_arn or "current credentials"
        if t.external_id:
            creds += " (with ExternalId)"
        table.add_row(t.account_id, t.name, ", ".join(t.regions) or "[red]none[/red]", creds)
    console.print(table)


def _print_summary(snapshot) -> None:
    table = Table(title="Discovery Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("Resources", str(snapshot.resource_count))
    table.add_row("Repositories", str(len(snapshot.repositories)))
    table.add_row("Pipelines", str(len(snapshot.pipelines)))
    table.add_row("Workloads", str(snapshot.workload_count))
    table.add_row("Coverage gaps", str(len(snapshot.coverage_gaps)))
    table.add_row("Errors", str(len(snapshot.errors)))
    console.print(table)

    if snapshot.coverage_gaps:
        console.print("\n[yellow]Coverage gaps:[/yellow]")
        for gap in snapshot.coverage_gaps:
            console.print(f"  • {gap.description}")

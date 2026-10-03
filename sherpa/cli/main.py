from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from sherpa.core.models import NamingConvention, ScanConfig
from sherpa.core.models.accounts import AwsAccountsFile
from sherpa.core.store import InventoryStore
from sherpa.orchestrator import run_discovery

console = Console()

# Exit codes: 0 = complete scan, 1 = config error or failure, 2 = scan completed but one or
# more scopes (e.g. AWS accounts) were skipped because their identity could not be confirmed.
EXIT_INCOMPLETE_SCAN = 2


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
@click.option("--db", "db_path", default=":memory:", show_default=True, help="SQLite DB path.")
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

    store = InventoryStore(db_path)
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

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from sherpa.core.models import NamingConvention, ScanConfig
from sherpa.core.store import InventoryStore
from sherpa.orchestrator import run_discovery

console = Console()


@click.group()
def cli() -> None:
    """Sherpa — M&A cloud migration discovery tool."""


@cli.command()
@click.option("--aws-account", "aws_accounts", multiple=True, help="AWS account ID(s) to scan.")
@click.option(
    "--regions",
    default="us-east-1",
    show_default=True,
    help="Comma-separated AWS regions.",
)
@click.option("--assume-role", "assume_role_arn", default=None, help="IAM role ARN to assume.")
@click.option("--github-org", default=None, help="GitHub org to scan for repos and pipelines.")
@click.option(
    "--github-token", envvar="GITHUB_TOKEN", default=None, help="GitHub personal access token."
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
    regions: str,
    assume_role_arn: str | None,
    github_org: str | None,
    github_token: str | None,
    output_dir: str,
    db_path: str,
    naming_convention_file: str | None,
) -> None:
    """Run a full discovery scan and produce an inventory snapshot."""
    aws_region_list = [r.strip() for r in regions.split(",") if r.strip()]

    naming = (
        NamingConvention.from_file(naming_convention_file)
        if naming_convention_file
        else NamingConvention()
    )

    config = ScanConfig(
        aws_accounts=list(aws_accounts),
        aws_regions=aws_region_list,
        assume_role_arn=assume_role_arn,
        github_org=github_org,
        github_token=github_token,
        naming_convention=naming,
    )

    store = InventoryStore(db_path)
    out_path = Path(output_dir)

    console.print("[bold cyan]Sherpa[/bold cyan] starting discovery…")
    console.print(f"  AWS accounts : {list(aws_accounts) or '(none)'}")
    console.print(f"  Regions      : {aws_region_list}")
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

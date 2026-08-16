from __future__ import annotations

import re

import yaml
from github import Github, GithubException

from sherpa.core.interfaces import ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import CoverageGap, Pipeline, PipelineStage, ScanConfig

_AWS_DEPLOY_ACTIONS = {
    "aws-actions/configure-aws-credentials",
    "aws-actions/amazon-ecs-deploy-task-definition",
    "aws-actions/amazon-ecs-render-task-definition",
}
_CDK_DEPLOY_PATTERN = re.compile(r"cdk\s+deploy", re.IGNORECASE)
_TF_APPLY_PATTERN = re.compile(r"terraform\s+apply", re.IGNORECASE)
_AWS_ACCOUNT_PATTERN = re.compile(r"\b(\d{12})\b")
# Non-capturing groups so findall() returns the full region string, not a tuple of parts
_AWS_REGION_PATTERN = re.compile(
    r"\b(?:us|eu|ap|sa|ca|af|me)-(?:east|west|central|south|north|northeast|southeast)-[1-9]\b"
)


def _extract_deploy_actions(job_steps: list[dict]) -> list[str]:
    found = []
    for step in job_steps:
        uses = step.get("uses", "")
        if uses:
            action_name = uses.split("@")[0]
            if action_name in _AWS_DEPLOY_ACTIONS:
                found.append(action_name)
        run = step.get("run", "")
        if _CDK_DEPLOY_PATTERN.search(run):
            found.append("cdk-deploy")
        if _TF_APPLY_PATTERN.search(run):
            found.append("terraform-apply")
    return found


def _extract_accounts_regions(workflow_text: str) -> tuple[list[str], list[str]]:
    accounts = list(dict.fromkeys(_AWS_ACCOUNT_PATTERN.findall(workflow_text)))
    regions = list(dict.fromkeys(_AWS_REGION_PATTERN.findall(workflow_text)))
    return accounts, regions


def _parse_workflow(content: str, repo_id: str, filename: str) -> Pipeline | None:
    try:
        data = yaml.safe_load(content)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    pipeline_id = f"{repo_id}/.github/workflows/{filename}"
    # PyYAML 1.1 parses bare `on` as boolean True; check both key forms.
    on_triggers = data.get("on") or data.get(True, {})
    if isinstance(on_triggers, dict):
        trigger_types = list(on_triggers.keys())
    elif isinstance(on_triggers, list):
        trigger_types = [str(t) for t in on_triggers]
    elif isinstance(on_triggers, str):
        trigger_types = [on_triggers]
    else:
        trigger_types = []

    jobs = data.get("jobs", {})
    stages: list[PipelineStage] = []
    all_accounts: list[str] = []
    all_regions: list[str] = []

    for job_name, job_def in (jobs or {}).items():
        if not isinstance(job_def, dict):
            continue
        steps = job_def.get("steps", []) or []
        deploy_actions = _extract_deploy_actions(steps)
        accts, regions = _extract_accounts_regions(str(job_def))
        all_accounts.extend(accts)
        all_regions.extend(regions)
        stages.append(
            PipelineStage(
                name=job_name,
                trigger_type=", ".join(trigger_types),
                aws_deploy_actions=deploy_actions,
                target_accounts=accts,
                target_regions=regions,
            )
        )

    return Pipeline(
        id=pipeline_id,
        pipeline_type="github_actions",
        repo_id=repo_id,
        stages=stages,
        deploys_to_resource_ids=[],
        deploys_to_accounts=list(dict.fromkeys(all_accounts)),
    )


class GithubActionsScanner(ScannerPlugin):
    @property
    def scanner_type(self) -> str:
        return "github-actions-pipeline"

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        if not config.github_org:
            return ValidationResult.fail(
                "github_org is required for github-actions-pipeline scanner"
            )
        return ValidationResult.ok()

    async def scan(self, config: ScanConfig) -> ScanResult:
        gh = Github(config.github_token)
        pipelines: list[Pipeline] = []
        coverage_gaps: list[CoverageGap] = []
        errors: list[str] = []

        try:
            org = gh.get_organization(config.github_org)
        except GithubException as exc:
            return ScanResult(
                scanner_type=self.scanner_type,
                errors=[f"Cannot access org {config.github_org}: {exc}"],
            )

        for repo in sorted(org.get_repos(), key=lambda r: r.full_name):
            repo_id = f"github.com/{repo.full_name}"
            try:
                workflows_dir = repo.get_contents(".github/workflows")
                if not isinstance(workflows_dir, list):
                    workflows_dir = [workflows_dir]
                for wf_file in sorted(workflows_dir, key=lambda f: f.name):
                    if not wf_file.name.endswith((".yml", ".yaml")):
                        continue
                    try:
                        content = wf_file.decoded_content.decode("utf-8", errors="replace")
                        pipeline = _parse_workflow(content, repo_id, wf_file.name)
                        if pipeline:
                            pipelines.append(pipeline)
                    except Exception as exc:
                        errors.append(f"{repo_id}/{wf_file.name}: {exc}")
            except GithubException:
                pass  # no workflows dir — expected for many repos

        return ScanResult(
            scanner_type=self.scanner_type,
            pipelines=pipelines,
            coverage_gaps=coverage_gaps,
            errors=errors,
        )

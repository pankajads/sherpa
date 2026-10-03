from __future__ import annotations

import re

import yaml
from github import Github, GithubException

from sherpa.core.interfaces import ScannerPlane, ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import (
    CoverageGap,
    DeployTarget,
    ErrorClass,
    Pipeline,
    PipelineStage,
    ResourceType,
    ScanConfig,
)
from sherpa.scanners._github_errors import classify

_AWS_DEPLOY_ACTIONS = {
    "aws-actions/configure-aws-credentials",
    "aws-actions/amazon-ecs-deploy-task-definition",
    "aws-actions/amazon-ecs-render-task-definition",
}
_CDK_DEPLOY_PATTERN = re.compile(r"cdk\s+deploy", re.IGNORECASE)
_TF_APPLY_PATTERN = re.compile(r"terraform\s+apply", re.IGNORECASE)
# Account IDs only from labelled places (C-6: a bare 12-digit number can be anything).
_ARN_ACCOUNT_PATTERN = re.compile(r"\barn:aws[\w-]*:[\w-]+:[\w-]*:(\d{12}):")
_ECR_ACCOUNT_PATTERN = re.compile(r"\b(\d{12})\.dkr\.ecr\.")
_ACCOUNT_ID_VALUE = re.compile(r"^\d{12}$")
# Non-capturing groups so findall() returns the full region string, not a tuple of parts
_AWS_REGION_PATTERN = re.compile(
    r"\b(?:us|eu|ap|sa|ca|af|me)-(?:east|west|central|south|north|northeast|southeast)-[1-9]\b"
)

_ROLE_ARN = re.compile(r"^arn:aws[\w-]*:iam::\d{12}:role/[\w+=,.@/-]+$")
_ROLE_NAME = re.compile(r"^[\w+=,.@-]{1,64}$")
# Explicit AWS CLI deploy commands: (command regex, option holding the target, resource type).
_CLI_TARGETS = [
    (
        re.compile(
            r"\baws\s+lambda\s+(?:update-function-code|update-function-configuration"
            r"|publish-version|update-alias)\b[^\n;&|]*?--function-name[\s=]+([^\s;&|]+)"
        ),
        "aws lambda --function-name",
        ResourceType.LAMBDA_FUNCTION,
    ),
    (
        re.compile(r"\baws\s+ecs\s+update-service\b[^\n;&|]*?--cluster[\s=]+([^\s;&|]+)"),
        "aws ecs update-service --cluster",
        ResourceType.ECS_CLUSTER,
    ),
    (
        re.compile(r"\baws\s+s3\s+(?:sync|cp)\s+\S+\s+s3://([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])"),
        "aws s3 sync/cp destination",
        ResourceType.S3_BUCKET,
    ),
]


def _is_literal(value: str) -> bool:
    """False for values only known at run time (${{ }} expressions, shell variables)."""
    return bool(value) and "${{" not in value and "$" not in value


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


def _extract_deploy_targets(job_steps: list[dict]) -> list[DeployTarget]:
    """Resources a job names explicitly. Anything not named is not linked (see C-6)."""
    targets: list[DeployTarget] = []
    for step in job_steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("uses", "")).split("@")[0]
        inputs = step.get("with") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        if action == "aws-actions/configure-aws-credentials":
            role = str(inputs.get("role-to-assume", "")).strip()
            if _ROLE_ARN.match(role):
                targets.append(
                    DeployTarget(
                        resource_type=ResourceType.IAM_ROLE,
                        value=role,
                        method="configure-aws-credentials:role-to-assume",
                    )
                )
            elif _is_literal(role) and _ROLE_NAME.match(role):
                # A bare name: the action resolves it in the signed-in account, which may
                # not be known here.
                targets.append(
                    DeployTarget(
                        resource_type=ResourceType.IAM_ROLE,
                        value=role,
                        method="configure-aws-credentials:role-to-assume",
                        confidence="medium",
                    )
                )
        elif action == "aws-actions/amazon-ecs-deploy-task-definition":
            cluster = str(inputs.get("cluster", "")).strip()
            if _is_literal(cluster):
                targets.append(
                    DeployTarget(
                        resource_type=ResourceType.ECS_CLUSTER,
                        value=cluster,
                        method="amazon-ecs-deploy-task-definition:cluster",
                    )
                )

        run = str(step.get("run", "")).replace("\\\n", " ")
        for pattern, method, resource_type in _CLI_TARGETS:
            for match in pattern.finditer(run):
                value = match.group(1).strip("'\"")
                if _is_literal(value):
                    targets.append(
                        DeployTarget(resource_type=resource_type, value=value, method=method)
                    )
    return targets


def _labelled_accounts(node: object, out: list[str]) -> None:
    """Account IDs under keys that say so, e.g. `AWS_ACCOUNT_ID: '123456789012'`."""
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                "account" in str(key).lower()
                and isinstance(value, str | int)
                and _ACCOUNT_ID_VALUE.match(str(value))
            ):
                out.append(str(value))
            _labelled_accounts(value, out)
    elif isinstance(node, list):
        for item in node:
            _labelled_accounts(item, out)


def _extract_accounts_regions(job_def: dict) -> tuple[list[str], list[str]]:
    text = str(job_def)
    accounts = _ARN_ACCOUNT_PATTERN.findall(text) + _ECR_ACCOUNT_PATTERN.findall(text)
    _labelled_accounts(job_def, accounts)
    regions = _AWS_REGION_PATTERN.findall(text)
    return list(dict.fromkeys(accounts)), list(dict.fromkeys(regions))


def _parse_workflow(content: str, repo_id: str, filename: str) -> Pipeline:
    """Parse one workflow file. Raises ValueError when it isn't a workflow (caller records it)."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("not a workflow definition (top level is not a mapping)")

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
        accts, regions = _extract_accounts_regions(job_def)
        all_accounts.extend(accts)
        all_regions.extend(regions)
        stages.append(
            PipelineStage(
                name=job_name,
                trigger_type=", ".join(trigger_types),
                aws_deploy_actions=deploy_actions,
                target_accounts=accts,
                target_regions=regions,
                deploy_targets=_extract_deploy_targets(steps),
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

    @property
    def plane(self) -> ScannerPlane:
        return ScannerPlane.PIPELINE

    def applies_to(self, config: ScanConfig) -> bool:
        return bool(config.github_org)

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        if not config.github_org:
            return ValidationResult.fail(
                "github_org is required for github-actions-pipeline scanner"
            )
        return ValidationResult.ok()

    async def scan(self, config: ScanConfig) -> ScanResult:
        gh = Github(config.github_token_value())
        pipelines: list[Pipeline] = []
        coverage_gaps: list[CoverageGap] = []
        errors: list[str] = []

        try:
            org = gh.get_organization(config.github_org)
        except GithubException as exc:
            return ScanResult(
                scanner_type=self.scanner_type,
                errors=[f"Cannot access org {config.github_org}: {exc}"],
                coverage_gaps=[
                    CoverageGap(
                        description=f"GitHub org {config.github_org} could not be read: "
                        "no workflows were scanned",
                        severity="error",
                        scanner=self.scanner_type,
                        scope=f"github.com/{config.github_org}",
                        error_class=classify(exc),
                    )
                ],
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
                        pipelines.append(_parse_workflow(content, repo_id, wf_file.name))
                    except Exception as exc:  # noqa: BLE001 - reported as a coverage gap
                        errors.append(f"{repo_id}/{wf_file.name}: {exc}")
                        error_class = (
                            ErrorClass.INVALID_CONTENT
                            if isinstance(exc, ValueError)
                            else classify(exc)
                        )
                        coverage_gaps.append(
                            CoverageGap(
                                description=(
                                    f"Workflow {wf_file.name} in {repo_id} could not be read: "
                                    "its deployment targets are missing"
                                ),
                                severity="warning",
                                scanner=self.scanner_type,
                                scope=repo_id,
                                error_class=error_class,
                            )
                        )
            except GithubException as exc:
                if exc.status == 404:
                    continue  # no workflows directory: expected for many repos, not a gap
                errors.append(f"{repo_id}/.github/workflows: {exc}")
                coverage_gaps.append(
                    CoverageGap(
                        description=(
                            f"Workflows of {repo_id} could not be listed: its pipelines are missing"
                        ),
                        severity="warning",
                        scanner=self.scanner_type,
                        scope=repo_id,
                        error_class=classify(exc),
                    )
                )

        return ScanResult(
            scanner_type=self.scanner_type,
            pipelines=pipelines,
            coverage_gaps=coverage_gaps,
            errors=errors,
        )

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from .enums import (
    DependencyPlane,
    DependencyType,
    ErrorClass,
    IaCType,
    MigrationPath,
    ResourceType,
)
from .naming import NamingConvention

# Determinism (CLAUDE.md): every list in a persisted model is sorted on construction and
# every dict is serialised with sorted keys, so output never depends on API response order.
# Note: `model_copy(update=...)` skips validation — build new models instead when lists change.


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def workload_id(name: str) -> str:
    """Content-derived workload ID: identical names give identical IDs across runs."""
    return "wl-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


class ResourceDependency(BaseModel):
    source_id: str
    target_id: str
    dependency_type: DependencyType
    plane: DependencyPlane
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


def dependency_sort_key(dep: ResourceDependency) -> tuple[str, str, str, str, str]:
    return (
        dep.source_id,
        dep.target_id,
        str(dep.dependency_type),
        str(dep.plane),
        _canonical(dep.metadata),
    )


class Resource(BaseModel):
    id: str  # ARN for AWS resources
    resource_type: ResourceType | str
    region: str
    account_id: str
    name: str = ""
    tags: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[ResourceDependency] = Field(default_factory=list)

    model_config = {"frozen": True}

    @field_validator("tags")
    @classmethod
    def _sort_tags(cls, v: dict[str, str]) -> dict[str, str]:
        return dict(sorted(v.items()))

    @field_validator("dependencies")
    @classmethod
    def _sort_dependencies(cls, v: list[ResourceDependency]) -> list[ResourceDependency]:
        return sorted(v, key=dependency_sort_key)


class PackageDependency(BaseModel):
    name: str
    version_spec: str = ""
    ecosystem: str  # npm, pypi, maven, go

    model_config = {"frozen": True}


class DeployTarget(BaseModel):
    """A resource a pipeline job names explicitly as what it deploys to (with provenance).

    `value` is an ARN when the workflow gives one, otherwise a resource name that the
    cross-plane linker resolves against the inventory.
    """

    resource_type: ResourceType
    value: str
    method: str  # how it was found, e.g. "configure-aws-credentials:role-to-assume"
    confidence: str = "high"  # high, medium, low

    model_config = {"frozen": True}


class PipelineStage(BaseModel):
    name: str
    trigger_type: str = ""  # push, pull_request, schedule, workflow_dispatch
    aws_deploy_actions: list[str] = Field(default_factory=list)
    target_accounts: list[str] = Field(default_factory=list)
    target_regions: list[str] = Field(default_factory=list)
    deploy_targets: list[DeployTarget] = Field(default_factory=list)

    model_config = {"frozen": True}

    @field_validator("aws_deploy_actions", "target_accounts", "target_regions")
    @classmethod
    def _sort_lists(cls, v: list[str]) -> list[str]:
        return sorted(v)

    @field_validator("deploy_targets")
    @classmethod
    def _sort_targets(cls, v: list[DeployTarget]) -> list[DeployTarget]:
        unique = {_canonical(t.model_dump(mode="json")): t for t in v}
        return [unique[k] for k in sorted(unique)]


class Repository(BaseModel):
    id: str  # "{host}/{org}/{repo}"
    url: str
    iac_type: IaCType = IaCType.NONE
    declared_resource_ids: list[str] = Field(default_factory=list)
    package_dependencies: list[PackageDependency] = Field(default_factory=list)
    has_dockerfile: bool = False
    has_docker_compose: bool = False
    default_branch: str = "main"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}

    @field_validator("declared_resource_ids")
    @classmethod
    def _sort_ids(cls, v: list[str]) -> list[str]:
        return sorted(v)

    @field_validator("package_dependencies")
    @classmethod
    def _sort_packages(cls, v: list[PackageDependency]) -> list[PackageDependency]:
        return sorted(v, key=lambda p: (p.ecosystem, p.name, p.version_spec))


class Pipeline(BaseModel):
    id: str  # "{repo_id}/.github/workflows/{filename}"
    pipeline_type: str  # github_actions, jenkins
    repo_id: str
    stages: list[PipelineStage] = Field(default_factory=list)
    deploys_to_resource_ids: list[str] = Field(default_factory=list)
    deploys_to_accounts: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}

    @field_validator("stages")
    @classmethod
    def _sort_stages(cls, v: list[PipelineStage]) -> list[PipelineStage]:
        return sorted(v, key=lambda st: (st.name, st.trigger_type, _canonical(st.model_dump())))

    @field_validator("deploys_to_resource_ids", "deploys_to_accounts")
    @classmethod
    def _sort_lists(cls, v: list[str]) -> list[str]:
        return sorted(v)


class Workload(BaseModel):
    id: str = ""  # defaults to workload_id(name); see docs/determinism.md
    name: str
    resource_ids: list[str] = Field(default_factory=list)
    repo_ids: list[str] = Field(default_factory=list)
    pipeline_ids: list[str] = Field(default_factory=list)
    inferred_from: str = ""  # tag, iac_module, name_prefix
    migration_path: MigrationPath = MigrationPath.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}

    @model_validator(mode="before")
    @classmethod
    def _default_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("id") and data.get("name") is not None:
            data = {**data, "id": workload_id(data["name"])}
        return data

    @field_validator("resource_ids", "repo_ids", "pipeline_ids")
    @classmethod
    def _sort_ids(cls, v: list[str]) -> list[str]:
        return sorted(v)


class CoverageGap(BaseModel):
    """Something the inventory may be missing, and why. Every caught error becomes one."""

    description: str
    affected_regions: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    severity: str = "warning"  # info, warning, error
    scanner: str = ""  # scanner_type that reported it, e.g. "aws-cloud"
    scope: str = ""  # what was being read, e.g. "aws-account:111111111111", "github.com/acme/api"
    error_class: ErrorClass = ErrorClass.NONE

    model_config = {"frozen": True}

    @field_validator("affected_regions", "affected_services")
    @classmethod
    def _sort_lists(cls, v: list[str]) -> list[str]:
        return sorted(v)


def coverage_gap_sort_key(gap: CoverageGap) -> tuple[int, str, str]:
    return (_SEVERITY_RANK.get(gap.severity, 3), gap.description, _canonical(gap.model_dump()))


_ACCOUNT_ID = re.compile(r"^\d{12}$")
_ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::(\d{12}):role/.+$")
DEFAULT_ROLE_NAME = "SherpaReadOnly"


def _role_arn_account(arn: str) -> str | None:
    m = _ROLE_ARN.match(arn)
    return m.group(1) if m else None


class AwsAccountSettings(BaseModel):
    """Per-account overrides; anything unset falls back to the scan-wide setting."""

    name: str = ""  # human label, e.g. "prod"
    regions: list[str] = Field(default_factory=list)
    role_arn: str | None = None
    role_name: str | None = None
    external_id: str | None = None

    model_config = {"frozen": True}


class AwsTarget(BaseModel):
    """One account, fully resolved: where to scan and how to get credentials."""

    account_id: str
    name: str = ""
    regions: list[str]
    role_arn: str | None  # None = use the current credentials, no role assumption
    external_id: str | None = None

    model_config = {"frozen": True}


class ScanConfig(BaseModel):
    aws_accounts: list[str] = Field(default_factory=list)
    aws_regions: list[str] = Field(default_factory=list)  # default regions for every account
    # Legacy: one explicit role ARN. Only valid when scanning a single account.
    assume_role_arn: str | None = None
    # Role assumed in every account as arn:aws:iam::<account>:role/<aws_role_name>.
    aws_role_name: str | None = None
    aws_external_id: str | None = None
    aws_account_settings: dict[str, AwsAccountSettings] = Field(default_factory=dict)
    github_org: str | None = None
    # Credentials are held in memory only: excluded from every dump/serialisation and
    # masked in repr, so they never reach snapshots, reports, the store or logs.
    github_token: SecretStr | None = Field(default=None, exclude=True, repr=False)
    service_categories: list[str] = Field(
        default_factory=lambda: ["compute", "storage", "networking", "messaging", "security"]
    )
    naming_convention: NamingConvention = Field(default_factory=NamingConvention)

    model_config = {"frozen": True}

    @model_validator(mode="before")
    @classmethod
    def _merge_account_lists(cls, data: Any) -> Any:
        # Accounts named only in per-account settings are still scanned.
        if isinstance(data, dict) and data.get("aws_account_settings"):
            accounts = set(data.get("aws_accounts") or []) | set(data["aws_account_settings"])
            data = {**data, "aws_accounts": sorted(accounts)}
        return data

    @model_validator(mode="after")
    def at_least_one_source(self) -> ScanConfig:
        if not self.aws_accounts and not self.github_org:
            raise ValueError("At least one of aws_accounts or github_org must be provided")
        return self

    @model_validator(mode="after")
    def _validate_aws_access(self) -> ScanConfig:
        bad = [a for a in self.aws_accounts if not _ACCOUNT_ID.match(a)]
        if bad:
            raise ValueError(f"AWS account IDs must be 12 digits: {', '.join(bad)}")
        if self.assume_role_arn and len(self.aws_accounts) > 1:
            raise ValueError(
                "assume_role_arn names one role, so it can only be used with a single account; "
                "use aws_role_name or per-account role_arn for several accounts"
            )
        for target in self.aws_targets():
            if target.role_arn is None:
                continue
            owner = _role_arn_account(target.role_arn)
            if owner is None:
                raise ValueError(f"Not an IAM role ARN: {target.role_arn}")
            if owner != target.account_id:
                raise ValueError(
                    f"Role {target.role_arn} belongs to account {owner}, not {target.account_id}"
                )
        return self

    def aws_targets(self) -> list[AwsTarget]:
        """Resolve every AWS account to its regions and credential source, sorted by account.

        Role precedence per account: settings.role_arn > settings.role_name > aws_role_name
        > assume_role_arn (single account) > current credentials (single account only)
        > the default role name (several accounts, so no account is scanned with another's
        credentials).
        """
        targets = []
        multi = len(self.aws_accounts) > 1
        for account in sorted(self.aws_accounts):
            s = self.aws_account_settings.get(account, AwsAccountSettings())
            role_name = s.role_name or self.aws_role_name
            role_arn = s.role_arn
            if role_arn is None and role_name:
                role_arn = f"arn:aws:iam::{account}:role/{role_name}"
            if role_arn is None and self.assume_role_arn:
                role_arn = self.assume_role_arn
            if role_arn is None and multi:
                role_arn = f"arn:aws:iam::{account}:role/{DEFAULT_ROLE_NAME}"
            targets.append(
                AwsTarget(
                    account_id=account,
                    name=s.name,
                    regions=sorted(set(s.regions or self.aws_regions)),
                    role_arn=role_arn,
                    external_id=s.external_id or self.aws_external_id,
                )
            )
        return targets

    def github_token_value(self) -> str | None:
        """Return the raw GitHub token for authenticating API clients. Never persist it."""
        return self.github_token.get_secret_value() if self.github_token else None


class ScanIdentity(BaseModel):
    """Who scanned what: the verified principal behind each scanned scope (audit trail).

    `scope` names what was scanned, e.g. "aws-account:111111111111". `verified` is False when
    the scope was skipped because its identity could not be confirmed; `detail` says why.
    """

    scope: str
    principal: str = ""  # e.g. arn:aws:sts::111111111111:assumed-role/SherpaReadOnly/...
    method: str = ""  # e.g. "assumed-role", "current-credentials"
    verified: bool = False
    detail: str = ""

    model_config = {"frozen": True}


class InventorySnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    config: ScanConfig
    resources: list[Resource] = Field(default_factory=list)
    repositories: list[Repository] = Field(default_factory=list)
    pipelines: list[Pipeline] = Field(default_factory=list)
    workloads: list[Workload] = Field(default_factory=list)
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    scan_identities: list[ScanIdentity] = Field(default_factory=list)

    model_config = {"frozen": True}

    @field_validator("scan_identities")
    @classmethod
    def _sort_identities(cls, v: list[ScanIdentity]) -> list[ScanIdentity]:
        return sorted(v, key=lambda i: (i.scope, i.principal))

    @property
    def unverified_scopes(self) -> list[str]:
        """Scopes skipped because their identity could not be confirmed."""
        return [i.scope for i in self.scan_identities if not i.verified]

    @field_validator("resources", "repositories", "pipelines")
    @classmethod
    def _sort_by_id(cls, v: list[Any]) -> list[Any]:
        return sorted(v, key=lambda x: x.id)

    @field_validator("workloads")
    @classmethod
    def _sort_workloads(cls, v: list[Workload]) -> list[Workload]:
        return sorted(v, key=lambda w: (w.name, w.id))

    @field_validator("coverage_gaps")
    @classmethod
    def _sort_gaps(cls, v: list[CoverageGap]) -> list[CoverageGap]:
        return sorted(v, key=coverage_gap_sort_key)

    @field_validator("errors")
    @classmethod
    def _sort_errors(cls, v: list[str]) -> list[str]:
        return sorted(v)

    def to_canonical_json(self) -> str:
        """Serialise with sorted keys at every level — the format of snapshot files."""
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"

    def close(self) -> InventorySnapshot:
        return self.model_copy(update={"completed_at": datetime.now(UTC)})

    @property
    def is_closed(self) -> bool:
        return self.completed_at is not None

    @property
    def resource_count(self) -> int:
        return len(self.resources)

    @property
    def workload_count(self) -> int:
        return len(self.workloads)

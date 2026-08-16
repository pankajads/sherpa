from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .enums import (
    DependencyPlane,
    DependencyType,
    IaCType,
    MigrationPath,
    ResourceType,
)
from .naming import NamingConvention


class ResourceDependency(BaseModel):
    source_id: str
    target_id: str
    dependency_type: DependencyType
    plane: DependencyPlane
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


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


class PackageDependency(BaseModel):
    name: str
    version_spec: str = ""
    ecosystem: str  # npm, pypi, maven, go

    model_config = {"frozen": True}


class PipelineStage(BaseModel):
    name: str
    trigger_type: str = ""  # push, pull_request, schedule, workflow_dispatch
    aws_deploy_actions: list[str] = Field(default_factory=list)
    target_accounts: list[str] = Field(default_factory=list)
    target_regions: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


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


class Pipeline(BaseModel):
    id: str  # "{repo_id}/.github/workflows/{filename}"
    pipeline_type: str  # github_actions, jenkins
    repo_id: str
    stages: list[PipelineStage] = Field(default_factory=list)
    deploys_to_resource_ids: list[str] = Field(default_factory=list)
    deploys_to_accounts: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class Workload(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    resource_ids: list[str] = Field(default_factory=list)
    repo_ids: list[str] = Field(default_factory=list)
    pipeline_ids: list[str] = Field(default_factory=list)
    inferred_from: str = ""  # tag, iac_module, name_prefix
    migration_path: MigrationPath = MigrationPath.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class CoverageGap(BaseModel):
    description: str
    affected_regions: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    severity: str = "warning"  # info, warning, error

    model_config = {"frozen": True}


class ScanConfig(BaseModel):
    aws_accounts: list[str] = Field(default_factory=list)
    aws_regions: list[str] = Field(default_factory=list)
    assume_role_arn: str | None = None
    github_org: str | None = None
    github_token: str | None = None
    service_categories: list[str] = Field(
        default_factory=lambda: ["compute", "storage", "networking", "messaging", "security"]
    )
    naming_convention: NamingConvention = Field(default_factory=NamingConvention)

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def at_least_one_source(self) -> ScanConfig:
        if not self.aws_accounts and not self.github_org:
            raise ValueError("At least one of aws_accounts or github_org must be provided")
        return self


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

    model_config = {"frozen": True}

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

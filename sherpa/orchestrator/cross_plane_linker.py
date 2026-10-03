"""Emit cross-plane dependency edges between cloud↔code↔pipeline entities."""

from __future__ import annotations

from sherpa.core.models import (
    DeployTarget,
    Pipeline,
    Repository,
    Resource,
    ResourceDependency,
)
from sherpa.core.models.enums import DependencyPlane, DependencyType, ResourceType
from sherpa.core.models.inventory import dependency_sort_key


def link_cross_plane(
    resources: list[Resource],
    repositories: list[Repository],
    pipelines: list[Pipeline],
) -> list[Resource]:
    """Return resources with additional cross-plane dependency edges appended."""
    resource_ids: set[str] = {r.id for r in resources}

    # code → cloud: repo declares a resource
    repo_edges: dict[str, list[ResourceDependency]] = {}
    for repo in repositories:
        for declared_id in repo.declared_resource_ids:
            if declared_id in resource_ids:
                dep = ResourceDependency(
                    source_id=repo.id,
                    target_id=declared_id,
                    dependency_type=DependencyType.DECLARES,
                    plane=DependencyPlane.CROSS,
                )
                repo_edges.setdefault(declared_id, []).append(dep)

    # pipeline → cloud: only resources a job names explicitly (C-6). Knowing which account a
    # pipeline deploys into is an account-level fact (Pipeline.deploys_to_accounts), never a
    # reason to link it to every resource in that account.
    pipe_edges: dict[str, list[ResourceDependency]] = {}
    for pipe in pipelines:
        for stage in pipe.stages:
            for target in stage.deploy_targets:
                for resource_id, confidence in _resolve(target, pipe, resources, resource_ids):
                    dep = ResourceDependency(
                        source_id=pipe.id,
                        target_id=resource_id,
                        dependency_type=DependencyType.DEPLOYS_TO,
                        plane=DependencyPlane.CROSS,
                        metadata={
                            "method": target.method,
                            "confidence": confidence,
                            "job": stage.name,
                        },
                    )
                    pipe_edges.setdefault(resource_id, []).append(dep)

    # Rebuild resources with extra edges (only if there are new edges). model_copy skips
    # validation, so sort explicitly to keep edge order independent of input order.
    updated: list[Resource] = []
    for r in resources:
        extra = repo_edges.get(r.id, []) + pipe_edges.get(r.id, [])
        if extra:
            deps = sorted([*r.dependencies, *extra], key=dependency_sort_key)
            updated.append(r.model_copy(update={"dependencies": deps}))
        else:
            updated.append(r)
    return sorted(updated, key=lambda r: r.id)


_LOWER = {"high": "medium", "medium": "low", "low": "low"}


def _resolve(
    target: DeployTarget, pipe: Pipeline, resources: list[Resource], resource_ids: set[str]
) -> list[tuple[str, str]]:
    """Inventory resources a deploy target refers to, each with the edge's confidence."""
    if target.value.startswith("arn:"):
        if target.value in resource_ids:
            return [(target.value, target.confidence)]
        if target.resource_type != ResourceType.IAM_ROLE:
            return []
        # A role ARN without its path (or with a different one): match by account and name.
        account = target.value.split(":")[4]
        name = target.value.rsplit("/", 1)[-1]
        candidates = [
            r
            for r in resources
            if r.resource_type == ResourceType.IAM_ROLE
            and r.account_id == account
            and r.name == name
        ]
        return [(r.id, _LOWER[target.confidence]) for r in candidates]

    candidates = [
        r for r in resources if r.resource_type == target.resource_type and r.name == target.value
    ]
    accounts = set(pipe.deploys_to_accounts)
    if accounts and any(r.account_id in accounts for r in candidates):
        candidates = [r for r in candidates if r.account_id in accounts]
    if len(candidates) == 1:
        return [(candidates[0].id, target.confidence)]
    # Same name in several accounts or regions and nothing to tell them apart: link each,
    # with low confidence, rather than guess one.
    return [(r.id, "low") for r in candidates]

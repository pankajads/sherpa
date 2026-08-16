"""Emit cross-plane dependency edges between cloud↔code↔pipeline entities."""

from __future__ import annotations

from sherpa.core.models import Pipeline, Repository, Resource, ResourceDependency
from sherpa.core.models.enums import DependencyPlane, DependencyType


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

    # pipeline → cloud: pipeline deploys to account (match by account ID prefix in resource ARN)
    pipe_edges: dict[str, list[ResourceDependency]] = {}
    for pipe in pipelines:
        for account in pipe.deploys_to_accounts:
            for r in resources:
                if r.account_id == account:
                    dep = ResourceDependency(
                        source_id=pipe.id,
                        target_id=r.id,
                        dependency_type=DependencyType.DEPLOYS_TO,
                        plane=DependencyPlane.CROSS,
                    )
                    pipe_edges.setdefault(r.id, []).append(dep)

    # Rebuild resources with extra edges (only if there are new edges)
    updated: list[Resource] = []
    for r in resources:
        extra = repo_edges.get(r.id, []) + pipe_edges.get(r.id, [])
        if extra:
            updated.append(r.model_copy(update={"dependencies": list(r.dependencies) + extra}))
        else:
            updated.append(r)
    return updated

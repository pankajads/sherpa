"""Rule-based workload grouping driven by NamingConvention."""

from __future__ import annotations

from sherpa.core.models import Repository, Resource, Workload
from sherpa.core.models.naming import NamingConvention


def infer_workloads(
    resources: list[Resource],
    repositories: list[Repository],
    naming: NamingConvention | None = None,
) -> list[Workload]:
    convention = naming or NamingConvention.default()
    groups: dict[str, dict] = {}

    for resource in resources:
        name, inferred_from = convention.resolve_workload_name(resource.name, resource.tags)
        if name not in groups:
            groups[name] = {
                "resource_ids": [],
                "repo_ids": [],
                "pipeline_ids": [],
                "inferred_from": inferred_from,
            }
        groups[name]["resource_ids"].append(resource.id)

    # Assign repos whose declared resources already belong to a group
    declared_to_group: dict[str, str] = {
        rid: wl_name for wl_name, g in groups.items() for rid in g["resource_ids"]
    }
    for repo in repositories:
        matched: set[str] = {
            declared_to_group[rid] for rid in repo.declared_resource_ids if rid in declared_to_group
        }
        for wl_name in matched:
            groups[wl_name]["repo_ids"].append(repo.id)

    return [
        Workload(
            name=name,
            resource_ids=sorted(g["resource_ids"]),
            repo_ids=sorted(set(g["repo_ids"])),
            pipeline_ids=sorted(set(g["pipeline_ids"])),
            inferred_from=g["inferred_from"],
        )
        for name, g in sorted(groups.items())
    ]

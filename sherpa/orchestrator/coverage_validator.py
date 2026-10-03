"""Check for scan coverage gaps."""

from __future__ import annotations

from sherpa.core.models import CoverageGap, Resource, ScanConfig
from sherpa.core.models.inventory import coverage_gap_sort_key

_EXPECTED_RESOURCE_TYPES_BY_CATEGORY = {
    "compute": {"aws::ec2::instance", "aws::lambda::function", "aws::ecs::cluster"},
    "storage": {"aws::s3::bucket", "aws::rds::db-instance", "aws::dynamodb::table"},
    "messaging": {"aws::sqs::queue", "aws::sns::topic"},
    "security": {"aws::iam::role"},
}


def validate_coverage(
    config: ScanConfig,
    resources: list[Resource],
    scan_errors: list[str],
) -> list[CoverageGap]:
    gaps: list[CoverageGap] = []

    found_types: set[str] = {str(r.resource_type) for r in resources}
    found: set[tuple[str, str]] = {(r.account_id, r.region) for r in resources}

    # Flag, per account, configured regions with no resources at all
    for target in config.aws_targets():
        empty_regions = [r for r in target.regions if (target.account_id, r) not in found]
        if empty_regions:
            gaps.append(
                CoverageGap(
                    description=(
                        f"No resources found in account {target.account_id} for one or more "
                        "configured regions — may indicate access issues"
                    ),
                    affected_regions=empty_regions,
                    severity="warning",
                )
            )

    # Flag service categories with no matching resources
    for category in config.service_categories:
        expected = _EXPECTED_RESOURCE_TYPES_BY_CATEGORY.get(category, set())
        if expected and not expected.intersection(found_types):
            gaps.append(
                CoverageGap(
                    description=f"No '{category}' resources found — collector may have insufficient permissions",
                    affected_services=[category],
                    severity="info",
                )
            )

    # Propagate scan errors as coverage gaps
    if scan_errors:
        gaps.append(
            CoverageGap(
                description=f"{len(scan_errors)} scanner error(s) — some resources may be missing",
                severity="error",
            )
        )

    return sorted(gaps, key=coverage_gap_sort_key)

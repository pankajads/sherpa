import pytest
from pydantic import ValidationError

from sherpa.core.models import (
    DependencyPlane,
    DependencyType,
    IaCType,
    InventorySnapshot,
    MigrationPath,
    NamingConvention,
    PackageDependency,
    Repository,
    Resource,
    ResourceDependency,
    ResourceType,
    ScanConfig,
    Workload,
)


def make_resource(**kwargs) -> Resource:
    defaults = dict(
        id="arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
        resource_type=ResourceType.EC2_INSTANCE,
        region="us-east-1",
        account_id="123456789012",
        name="web-server",
        tags={"Application": "checkout", "Environment": "prod"},
    )
    return Resource(**{**defaults, **kwargs})


class TestResource:
    def test_resource_is_frozen(self):
        r = make_resource()
        with pytest.raises(ValidationError):
            r.name = "changed"  # type: ignore[misc]

    def test_resource_tags_stored(self):
        r = make_resource(tags={"Application": "checkout"})
        assert r.tags["Application"] == "checkout"


class TestResourceDependency:
    def test_valid_dependency(self):
        dep = ResourceDependency(
            source_id="arn:aws:lambda:us-east-1:123:function/processor",
            target_id="arn:aws:sqs:us-east-1:123:queue/jobs",
            dependency_type=DependencyType.READS_FROM,
            plane=DependencyPlane.CLOUD,
        )
        assert dep.dependency_type == DependencyType.READS_FROM


class TestScanConfig:
    def test_requires_at_least_one_source(self):
        with pytest.raises(ValidationError):
            ScanConfig()

    def test_aws_only_is_valid(self):
        config = ScanConfig(aws_accounts=["123456789012"], aws_regions=["us-east-1"])
        assert config.aws_accounts == ["123456789012"]

    def test_github_only_is_valid(self):
        config = ScanConfig(github_org="acme")
        assert config.github_org == "acme"

    def test_default_service_categories_set(self):
        config = ScanConfig(aws_accounts=["123"])
        assert "compute" in config.service_categories

    def test_default_naming_convention_attached(self):
        config = ScanConfig(aws_accounts=["123"])
        assert isinstance(config.naming_convention, NamingConvention)

    def test_custom_naming_convention_stored(self):
        naming = NamingConvention(workload_tag_keys=["Team"], unassigned_label="no-team")
        config = ScanConfig(aws_accounts=["123"], naming_convention=naming)
        assert config.naming_convention.unassigned_label == "no-team"


class TestNamingConvention:
    def test_tag_lookup_first_priority(self):
        naming = NamingConvention(workload_tag_keys=["Team", "Application"])
        name, source = naming.resolve_workload_name(
            "prod-payments-api", {"Team": "platform", "Application": "payments"}
        )
        assert name == "platform"
        assert source == "tag"

    def test_tag_lookup_falls_through_missing_keys(self):
        naming = NamingConvention(workload_tag_keys=["Team", "Application"])
        name, source = naming.resolve_workload_name(
            "prod-payments-api", {"Application": "payments"}
        )
        assert name == "payments"
        assert source == "tag"

    def test_name_segment_first(self):
        naming = NamingConvention(workload_tag_keys=[], name_separator="-", name_part="first")
        name, source = naming.resolve_workload_name("checkout-api-prod", {})
        assert name == "checkout"
        assert source == "name_segment"

    def test_name_segment_last(self):
        naming = NamingConvention(workload_tag_keys=[], name_separator="-", name_part="last")
        name, source = naming.resolve_workload_name("prod-eu-west-payments", {})
        assert name == "payments"
        assert source == "name_segment"

    def test_name_segment_integer_index(self):
        naming = NamingConvention(workload_tag_keys=[], name_separator="-", name_part=1)
        name, source = naming.resolve_workload_name("prod-checkout-api", {})
        assert name == "checkout"
        assert source == "name_segment"

    def test_name_regex_override(self):
        naming = NamingConvention(
            workload_tag_keys=[],
            name_regex=r"^[^-]+-(?P<workload>[^-]+)-",
        )
        name, source = naming.resolve_workload_name("prod-payments-worker", {})
        assert name == "payments"
        assert source == "name_regex"

    def test_unassigned_label_when_no_match(self):
        naming = NamingConvention(
            workload_tag_keys=[],
            name_separator="-",
            name_part="first",
            unassigned_label="orphan",
        )
        name, source = naming.resolve_workload_name("singleword", {})
        assert name == "orphan"
        assert source == "unassigned"

    def test_invalid_regex_missing_workload_group(self):
        with pytest.raises(ValueError, match="named group called 'workload'"):
            NamingConvention(name_regex=r"^([^-]+)-")

    def test_underscore_separator(self):
        naming = NamingConvention(workload_tag_keys=[], name_separator="_", name_part="first")
        name, source = naming.resolve_workload_name("payments_api_prod", {})
        assert name == "payments"
        assert source == "name_segment"

    def test_custom_tag_keys_case_sensitive(self):
        naming = NamingConvention(workload_tag_keys=["squad"])
        name, source = naming.resolve_workload_name("anything", {"Squad": "infra"})
        # "Squad" ≠ "squad" — should fall through to name segment
        assert source != "tag"


class TestInventorySnapshot:
    def test_snapshot_starts_open(self):
        config = ScanConfig(aws_accounts=["123"])
        snap = InventorySnapshot(config=config)
        assert not snap.is_closed
        assert snap.completed_at is None

    def test_close_produces_new_frozen_snapshot(self):
        config = ScanConfig(aws_accounts=["123"])
        snap = InventorySnapshot(config=config)
        closed = snap.close()
        assert closed.is_closed
        assert closed.completed_at is not None
        assert not snap.is_closed  # original unchanged

    def test_resource_count(self):
        config = ScanConfig(aws_accounts=["123"])
        r = make_resource()
        snap = InventorySnapshot(config=config, resources=[r])
        assert snap.resource_count == 1


class TestWorkload:
    def test_workload_gets_uuid_by_default(self):
        w1 = Workload(name="checkout")
        w2 = Workload(name="checkout")
        assert w1.id != w2.id

    def test_migration_path_defaults_unknown(self):
        w = Workload(name="api")
        assert w.migration_path == MigrationPath.UNKNOWN


class TestRepository:
    def test_repo_id_format(self):
        repo = Repository(id="github.com/acme/api", url="https://github.com/acme/api")
        assert repo.iac_type == IaCType.NONE

    def test_package_dependencies_attached(self):
        dep = PackageDependency(name="boto3", version_spec=">=1.35", ecosystem="pypi")
        repo = Repository(
            id="github.com/acme/api",
            url="https://github.com/acme/api",
            package_dependencies=[dep],
        )
        assert repo.package_dependencies[0].name == "boto3"

from sherpa.core.models import NamingConvention, Repository, Resource, ResourceType
from sherpa.orchestrator.workload_inferrer import infer_workloads


def _res(name: str, tags: dict | None = None, rid: str | None = None) -> Resource:
    return Resource(
        id=rid or f"arn:aws:ec2:us-east-1:123:instance/{name}",
        resource_type=ResourceType.EC2_INSTANCE,
        region="us-east-1",
        account_id="123456789012",
        name=name,
        tags=tags or {},
    )


class TestInferWorkloads:
    def test_groups_by_tag(self):
        naming = NamingConvention(workload_tag_keys=["Team"])
        resources = [
            _res("r1", tags={"Team": "platform"}),
            _res("r2", tags={"Team": "payments"}),
            _res("r3", tags={"Team": "platform"}),
        ]
        workloads = infer_workloads(resources, [], naming)
        names = {w.name for w in workloads}
        assert names == {"platform", "payments"}
        platform = next(w for w in workloads if w.name == "platform")
        assert len(platform.resource_ids) == 2

    def test_falls_back_to_name_segment(self):
        naming = NamingConvention(workload_tag_keys=[], name_separator="-", name_part="first")
        resources = [
            _res("checkout-api"),
            _res("checkout-worker"),
            _res("auth-service"),
        ]
        workloads = infer_workloads(resources, [], naming)
        names = {w.name for w in workloads}
        assert "checkout" in names
        assert "auth" in names

    def test_unassigned_when_no_match(self):
        naming = NamingConvention(workload_tag_keys=[], unassigned_label="ungrouped")
        resources = [_res("singleword")]
        workloads = infer_workloads(resources, [], naming)
        assert workloads[0].name == "ungrouped"

    def test_repo_assigned_to_workload_via_declared_resource(self):
        naming = NamingConvention(workload_tag_keys=["Application"])
        rid = "arn:aws:lambda:us-east-1:123:function/payments-fn"
        resources = [_res("payments-fn", tags={"Application": "payments"}, rid=rid)]
        repo = Repository(
            id="github.com/acme/payments",
            url="https://github.com/acme/payments",
            declared_resource_ids=[rid],
        )
        workloads = infer_workloads(resources, [repo], naming)
        assert len(workloads) == 1
        assert repo.id in workloads[0].repo_ids

    def test_uses_default_convention_when_none_passed(self):
        resources = [_res("checkout-api", tags={"Application": "checkout"})]
        workloads = infer_workloads(resources, [])
        assert workloads[0].name == "checkout"

    def test_output_is_sorted_deterministically(self):
        naming = NamingConvention(workload_tag_keys=["Team"])
        resources = [
            _res("r1", tags={"Team": "zebra"}),
            _res("r2", tags={"Team": "alpha"}),
            _res("r3", tags={"Team": "mango"}),
        ]
        workloads = infer_workloads(resources, [], naming)
        assert [w.name for w in workloads] == ["alpha", "mango", "zebra"]

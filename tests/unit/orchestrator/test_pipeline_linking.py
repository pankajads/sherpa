"""Pipeline → cloud links come only from explicit deploy targets (issue #27, finding C-6)."""

from __future__ import annotations

from sherpa.core.models import DeployTarget, Pipeline, PipelineStage, Resource, ResourceType
from sherpa.orchestrator.cross_plane_linker import link_cross_plane

A, B = "111111111111", "222222222222"
PIPE_ID = "github.com/acme/svc/.github/workflows/deploy.yml"


def _res(rtype: ResourceType, name: str, account: str = A, rid: str | None = None) -> Resource:
    return Resource(
        id=rid or f"arn:aws:test:us-east-1:{account}:{rtype.value.rsplit('::', 1)[-1]}/{name}",
        resource_type=rtype,
        region="us-east-1",
        account_id=account,
        name=name,
    )


def _role(name: str, account: str = A, path: str = "/") -> Resource:
    return _res(
        ResourceType.IAM_ROLE, name, account, rid=f"arn:aws:iam::{account}:role{path}{name}"
    )


def _pipe(*targets: DeployTarget, accounts: tuple[str, ...] = ()) -> Pipeline:
    return Pipeline(
        id=PIPE_ID,
        pipeline_type="github_actions",
        repo_id="github.com/acme/svc",
        deploys_to_accounts=list(accounts),
        stages=[PipelineStage(name="deploy", deploy_targets=list(targets))],
    )


def _target(rtype: ResourceType, value: str, confidence: str = "high") -> DeployTarget:
    return DeployTarget(resource_type=rtype, value=value, method="m", confidence=confidence)


def _links(resources: list[Resource], pipe: Pipeline) -> dict[str, str]:
    """resource name -> confidence of its deploys_to edge from the pipeline."""
    out = {}
    for r in link_cross_plane(resources, [], [pipe]):
        for d in r.dependencies:
            if d.source_id == PIPE_ID:
                out[r.name] = d.metadata["confidence"]
    return out


def test_account_alone_links_nothing():
    # The C-6 regression: knowing the account is not a reason to link every resource in it.
    resources = [_res(ResourceType.EC2_INSTANCE, f"i-{n}") for n in range(5)]
    assert _links(resources, _pipe(accounts=(A,))) == {}


def test_exact_arn():
    role, other = _role("ci-deploy"), _role("payments-lambda")
    pipe = _pipe(_target(ResourceType.IAM_ROLE, role.id), accounts=(A,))
    assert _links([role, other], pipe) == {"ci-deploy": "high"}


def test_role_arn_with_different_path_matches_by_name_with_lower_confidence():
    role = _role("ci-deploy", path="/ci/")
    pipe = _pipe(_target(ResourceType.IAM_ROLE, f"arn:aws:iam::{A}:role/ci-deploy"))
    assert _links([role, _role("ci-deploy", account=B)], pipe) == {"ci-deploy": "medium"}


def test_arn_not_in_inventory_links_nothing():
    pipe = _pipe(_target(ResourceType.LAMBDA_FUNCTION, f"arn:aws:lambda:us-east-1:{A}:function:x"))
    assert _links([_res(ResourceType.LAMBDA_FUNCTION, "x")], pipe) == {}


def test_name_is_matched_within_its_type():
    fn = _res(ResourceType.LAMBDA_FUNCTION, "payments")
    bucket = _res(ResourceType.S3_BUCKET, "payments")
    pipe = _pipe(_target(ResourceType.LAMBDA_FUNCTION, "payments"))
    linked = link_cross_plane([fn, bucket], [], [pipe])
    assert [r.resource_type for r in linked if r.dependencies] == [ResourceType.LAMBDA_FUNCTION]


def test_name_in_several_accounts_narrowed_by_the_pipelines_accounts():
    prod = _res(ResourceType.LAMBDA_FUNCTION, "payments", account=A)
    dev = _res(ResourceType.LAMBDA_FUNCTION, "payments", account=B)
    pipe = _pipe(_target(ResourceType.LAMBDA_FUNCTION, "payments"), accounts=(B,))
    linked = {r.account_id: r for r in link_cross_plane([prod, dev], [], [pipe])}
    assert linked[B].dependencies and not linked[A].dependencies
    assert linked[B].dependencies[0].metadata["confidence"] == "high"


def test_ambiguous_name_links_each_candidate_with_low_confidence():
    prod = _res(ResourceType.LAMBDA_FUNCTION, "payments", account=A)
    dev = _res(ResourceType.LAMBDA_FUNCTION, "payments", account=B)
    pipe = _pipe(_target(ResourceType.LAMBDA_FUNCTION, "payments"))
    linked = link_cross_plane([prod, dev], [], [pipe])
    assert [d.metadata["confidence"] for r in linked for d in r.dependencies] == ["low", "low"]


def test_edge_records_method_confidence_and_job():
    role = _role("ci-deploy")
    pipe = _pipe(_target(ResourceType.IAM_ROLE, role.id))
    (linked,) = link_cross_plane([role], [], [pipe])
    (edge,) = linked.dependencies
    assert edge.metadata == {"method": "m", "confidence": "high", "job": "deploy"}
    assert (edge.source_id, edge.target_id, edge.dependency_type) == (
        PIPE_ID,
        role.id,
        "deploys_to",
    )

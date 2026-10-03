"""What a GitHub Actions workflow says it deploys to (issue #27, finding C-6).

Only explicit evidence counts: a role-to-assume ARN, an ECS deploy action's cluster, or an
AWS CLI command naming its target. Account IDs come only from ARNs, ECR registry hosts and
keys labelled as accounts; a bare 12-digit number is not an account.
"""

from __future__ import annotations

import yaml

from sherpa.core.models import DeployTarget, PipelineStage, ResourceType
from sherpa.scanners.pipeline.github_actions.scanner import _parse_workflow

ROLE = "arn:aws:iam::123456789012:role/ci-deploy-role"


def _job(steps: list[dict], **job: object) -> str:
    return yaml.safe_dump({"on": "push", "jobs": {"deploy": {"steps": steps, **job}}})


def _targets(steps: list[dict]) -> list[tuple[str, str, str, str]]:
    (stage,) = _parse_workflow(_job(steps), "github.com/acme/svc", "deploy.yml").stages
    return [(t.resource_type, t.value, t.method, t.confidence) for t in stage.deploy_targets]


def _accounts(workflow: str) -> list[str]:
    return _parse_workflow(workflow, "github.com/acme/svc", "deploy.yml").deploys_to_accounts


class TestDeployTargets:
    def test_role_to_assume_arn(self):
        steps = [
            {"uses": "aws-actions/configure-aws-credentials@v4", "with": {"role-to-assume": ROLE}}
        ]
        assert _targets(steps) == [
            (ResourceType.IAM_ROLE, ROLE, "configure-aws-credentials:role-to-assume", "high")
        ]

    def test_role_to_assume_bare_name_is_medium(self):
        steps = [
            {"uses": "aws-actions/configure-aws-credentials@v4", "with": {"role-to-assume": "ci"}}
        ]
        assert _targets(steps)[0][1:] == (
            "ci",
            "configure-aws-credentials:role-to-assume",
            "medium",
        )

    def test_run_time_values_are_not_targets(self):
        steps = [
            {
                "uses": "aws-actions/configure-aws-credentials@v4",
                "with": {"role-to-assume": "${{ secrets.DEPLOY_ROLE }}"},
            },
            {"run": "aws lambda update-function-code --function-name $FN --zip-file x.zip"},
            {
                "uses": "aws-actions/amazon-ecs-deploy-task-definition@v2",
                "with": {"cluster": "${{ vars.CLUSTER }}"},
            },
        ]
        assert _targets(steps) == []

    def test_ecs_deploy_action_cluster(self):
        steps = [
            {
                "uses": "aws-actions/amazon-ecs-deploy-task-definition@v2",
                "with": {"cluster": "checkout-cluster", "service": "web"},
            }
        ]
        assert _targets(steps) == [
            (
                ResourceType.ECS_CLUSTER,
                "checkout-cluster",
                "amazon-ecs-deploy-task-definition:cluster",
                "high",
            )
        ]

    def test_cli_commands(self):
        steps = [
            {
                "run": "aws lambda update-function-code \\\n"
                "  --function-name payments-processor --zip-file fileb://f.zip\n"
                "aws ecs update-service --service web --cluster=checkout-cluster\n"
                "aws s3 sync ./dist s3://checkout-assets --delete\n"
            }
        ]
        assert sorted(_targets(steps)) == [
            (
                ResourceType.ECS_CLUSTER,
                "checkout-cluster",
                "aws ecs update-service --cluster",
                "high",
            ),
            (
                ResourceType.LAMBDA_FUNCTION,
                "payments-processor",
                "aws lambda --function-name",
                "high",
            ),
            (ResourceType.S3_BUCKET, "checkout-assets", "aws s3 sync/cp destination", "high"),
        ]

    def test_s3_source_bucket_is_not_a_target(self):
        assert _targets([{"run": "aws s3 cp s3://build-artifacts/app.zip ./app.zip"}]) == []

    def test_plain_terraform_apply_names_nothing(self):
        # Which resources a Terraform apply manages isn't known from the workflow; guessing
        # (e.g. everything the repo mentions) is how C-6 started.
        assert _targets([{"run": "terraform apply -auto-approve"}]) == []

    def test_targets_are_sorted_and_deduplicated(self):
        steps = [
            {"run": "aws lambda update-function-code --function-name b"},
            {"run": "aws lambda update-function-code --function-name a"},
            {"run": "aws lambda update-function-code --function-name b"},
        ]
        assert [t[1] for t in _targets(steps)] == ["a", "b"]

    def test_model_sorts_targets_regardless_of_input_order(self):
        a = DeployTarget(resource_type=ResourceType.S3_BUCKET, value="a", method="m")
        b = DeployTarget(resource_type=ResourceType.IAM_ROLE, value="b", method="m")
        assert (
            PipelineStage(name="j", deploy_targets=[a, b]).deploy_targets
            == PipelineStage(name="j", deploy_targets=[b, a]).deploy_targets
        )


class TestAccounts:
    def test_bare_12_digit_numbers_are_not_accounts(self):
        workflow = _job(
            [{"run": "echo build 202610031200 && curl https://x/orders/123456789012"}],
            env={"ORDER_ID": "123456789012", "TIMESTAMP": 202610031200},
        )
        assert _accounts(workflow) == []

    def test_accounts_from_arns_ecr_hosts_and_labelled_keys(self):
        workflow = _job(
            [
                {
                    "uses": "aws-actions/configure-aws-credentials@v4",
                    "with": {"role-to-assume": ROLE},
                },
                {"run": "docker push 222222222222.dkr.ecr.eu-west-1.amazonaws.com/app:1"},
            ],
            env={"AWS_ACCOUNT_ID": "333333333333", "PROD_ACCOUNT": 444444444444},
        )
        assert _accounts(workflow) == [
            "123456789012",
            "222222222222",
            "333333333333",
            "444444444444",
        ]

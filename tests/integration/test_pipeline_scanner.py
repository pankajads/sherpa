"""Integration tests for GithubActionsScanner with mocked PyGithub objects."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from github import GithubException

from sherpa.core.models import ScanConfig
from sherpa.scanners.pipeline.github_actions.scanner import GithubActionsScanner

from .conftest import make_file_content, make_org, make_repo


def _config(**kwargs) -> ScanConfig:
    return ScanConfig(github_org="acme", **kwargs)


def _workflow_repo(full_name: str, workflows: dict[str, bytes]) -> MagicMock:
    """Build a repo mock with .github/workflows files."""
    repo = MagicMock()
    repo.full_name = full_name
    repo.default_branch = "main"

    wf_files = []
    for filename, content in workflows.items():
        f = make_file_content(content, name=filename)
        wf_files.append(f)

    def _get_contents(path, ref=None):
        if path == ".github/workflows":
            return wf_files
        raise GithubException(404, "Not Found", None)

    repo.get_contents.side_effect = _get_contents
    return repo


def _scanner_with_org(org_mock: MagicMock) -> tuple[GithubActionsScanner, MagicMock]:
    scanner = GithubActionsScanner()
    gh_mock = MagicMock()
    gh_mock.get_organization.return_value = org_mock
    return scanner, gh_mock


# ------------------------------------------------------------------ Deploy action detection


class TestDeployActionDetection:
    async def test_detects_configure_aws_credentials(self):
        workflow = b"""
on: [push]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-region: us-east-1
"""
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/api", {"deploy.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert len(result.pipelines) == 1
        deploy_actions = result.pipelines[0].stages[0].aws_deploy_actions
        assert "aws-actions/configure-aws-credentials" in deploy_actions

    async def test_detects_ecs_deploy_action(self):
        workflow = b"""
on: [push]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/amazon-ecs-deploy-task-definition@v1
"""
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/svc", {"ecs.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        actions = result.pipelines[0].stages[0].aws_deploy_actions
        assert "aws-actions/amazon-ecs-deploy-task-definition" in actions

    async def test_detects_cdk_deploy(self):
        workflow = b"""
on: [push]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: npx cdk deploy --all
"""
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/infra", {"deploy.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        actions = result.pipelines[0].stages[0].aws_deploy_actions
        assert "cdk-deploy" in actions

    async def test_detects_terraform_apply(self):
        workflow = b"""
on: [push]
jobs:
  tf:
    runs-on: ubuntu-latest
    steps:
      - run: terraform apply -auto-approve
"""
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/infra", {"tf.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        actions = result.pipelines[0].stages[0].aws_deploy_actions
        assert "terraform-apply" in actions

    async def test_no_deploy_actions_when_no_aws_steps(self):
        workflow = b"""
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/svc", {"test.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        # pipeline still emitted (it has a job), just no deploy actions
        assert len(result.pipelines) == 1
        assert result.pipelines[0].stages[0].aws_deploy_actions == []


# ------------------------------------------------------------------ Trigger detection


class TestTriggerDetection:
    async def test_detects_push_trigger(self):
        workflow = b"on:\n  push:\n    branches: [main]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/api", {"ci.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert "push" in result.pipelines[0].stages[0].trigger_type

    async def test_detects_schedule_trigger(self):
        workflow = b"on:\n  schedule:\n    - cron: '0 2 * * *'\njobs:\n  job:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/api", {"nightly.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert "schedule" in result.pipelines[0].stages[0].trigger_type


# ------------------------------------------------------------------ Account extraction


class TestAccountExtraction:
    async def test_extracts_12_digit_account_ids(self):
        workflow = (
            b"on: [push]\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n"
            b"    env:\n      AWS_ACCOUNT: '123456789012'\n"
            b"    steps:\n      - run: echo deploy\n"
        )
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/svc", {"deploy.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert "123456789012" in result.pipelines[0].deploys_to_accounts

    async def test_pipeline_id_includes_workflow_filename(self):
        workflow = b"on: [push]\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
        scanner, gh_mock = _scanner_with_org(
            make_org([_workflow_repo("acme/svc", {"release.yml": workflow})])
        )
        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.pipelines[0].id.endswith("release.yml")


# ------------------------------------------------------------------ Resilience


class TestPipelineResilience:
    async def test_repos_without_workflows_dir_silently_skipped(self):
        repo = make_repo("acme/no-ci", tree_paths=["main.py"])  # no workflows
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.pipelines == []
        assert result.errors == []

    async def test_multiple_workflows_in_one_repo(self):
        repo = _workflow_repo(
            "acme/svc",
            {
                "ci.yml": b"on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n",
                "deploy.yml": b"on: [push]\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo deploy\n",
            },
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert len(result.pipelines) == 2

    async def test_invalid_yaml_workflow_skipped_gracefully(self):
        repo = _workflow_repo(
            "acme/svc",
            {
                "good.yml": b"on: [push]\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
                "bad.yml": b"on: [\nnot: valid: yaml: [[\n",
            },
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.pipeline.github_actions.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        # good workflow still parsed
        assert len(result.pipelines) == 1

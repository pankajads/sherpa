"""Integration tests for GithubCodeScanner with mocked PyGithub objects."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sherpa.core.models import ScanConfig
from sherpa.core.models.enums import IaCType
from sherpa.scanners.code.github.scanner import GithubCodeScanner

from .conftest import make_org, make_repo


def _config(**kwargs) -> ScanConfig:
    return ScanConfig(github_org="acme", **kwargs)


def _scanner_with_org(org_mock: MagicMock) -> tuple[GithubCodeScanner, MagicMock]:
    scanner = GithubCodeScanner()
    gh_mock = MagicMock()
    gh_mock.get_organization.return_value = org_mock
    return scanner, gh_mock


# ------------------------------------------------------------------ IaC detection


class TestIaCDetection:
    async def test_detects_terraform(self):
        repo = make_repo(
            "acme/infra",
            tree_paths=["main.tf", "variables.tf"],
            file_contents={"main.tf": b'resource "aws_s3_bucket" "data" {}'},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert len(result.repositories) == 1
        assert result.repositories[0].iac_type == IaCType.TERRAFORM

    async def test_detects_cloudformation(self):
        cf_content = b"AWSTemplateFormatVersion: '2010-09-09'\nResources: {}"
        repo = make_repo(
            "acme/stack",
            tree_paths=["template.yaml"],
            file_contents={"template.yaml": cf_content},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.repositories[0].iac_type == IaCType.CLOUDFORMATION

    async def test_detects_cdk(self):
        repo = make_repo(
            "acme/cdk-app",
            tree_paths=["cdk.json", "lib/stack.ts"],
            file_contents={"cdk.json": b'{"app": "npx ts-node bin/app.ts"}'},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.repositories[0].iac_type == IaCType.CDK

    async def test_no_iac_when_no_marker_files(self):
        repo = make_repo("acme/plain-service", tree_paths=["src/main.py", "tests/test_main.py"])
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.repositories[0].iac_type == IaCType.NONE


# ------------------------------------------------------------------ ARN extraction


class TestArnExtraction:
    async def test_extracts_arns_from_terraform(self):
        tf_content = b"""
resource "aws_lambda_event_source_mapping" "example" {
  event_source_arn = "arn:aws:sqs:us-east-1:123456789012:queue/jobs"
  function_name    = "arn:aws:lambda:us-east-1:123456789012:function/processor"
}
"""
        repo = make_repo(
            "acme/infra",
            tree_paths=["main.tf"],
            file_contents={"main.tf": tf_content},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        declared = result.repositories[0].declared_resource_ids
        assert any("sqs" in arn for arn in declared)
        assert any("lambda" in arn for arn in declared)

    async def test_declared_resource_ids_are_sorted_and_deduplicated(self):
        arn = "arn:aws:s3:::my-bucket"
        tf_content = f"{arn}\n{arn}\n{arn}".encode()
        repo = make_repo(
            "acme/infra",
            tree_paths=["main.tf"],
            file_contents={"main.tf": tf_content},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        declared = result.repositories[0].declared_resource_ids
        assert declared.count(arn) == 1


# ------------------------------------------------------------------ Package deps


class TestPackageDependencies:
    async def test_parses_requirements_txt(self):
        content = b"boto3>=1.35\nrequests==2.31.0\n# comment\n\npandas"
        repo = make_repo(
            "acme/service",
            tree_paths=["requirements.txt"],
            file_contents={"requirements.txt": content},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        deps = {d.name: d for d in result.repositories[0].package_dependencies}
        assert "boto3" in deps
        assert deps["boto3"].version_spec == ">=1.35"
        assert deps["boto3"].ecosystem == "pypi"
        assert "requests" in deps
        assert "pandas" in deps

    async def test_parses_package_json(self):
        content = (
            b'{"dependencies":{"express":"^4.18","axios":"1.6.0"},"devDependencies":{"jest":"^29"}}'
        )
        repo = make_repo(
            "acme/frontend",
            tree_paths=["package.json"],
            file_contents={"package.json": content},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        deps = {d.name: d for d in result.repositories[0].package_dependencies}
        assert "express" in deps
        assert deps["express"].ecosystem == "npm"
        assert "jest" in deps

    async def test_parses_go_mod(self):
        content = b"""module github.com/acme/service

go 1.21

require (
    github.com/aws/aws-sdk-go-v2 v1.24.0
    github.com/gin-gonic/gin v1.9.1
)
"""
        repo = make_repo(
            "acme/go-svc",
            tree_paths=["go.mod"],
            file_contents={"go.mod": content},
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        deps = {d.name: d for d in result.repositories[0].package_dependencies}
        assert "github.com/aws/aws-sdk-go-v2" in deps
        assert deps["github.com/aws/aws-sdk-go-v2"].ecosystem == "go"


# ------------------------------------------------------------------ Container detection


class TestContainerDetection:
    async def test_detects_dockerfile(self):
        repo = make_repo(
            "acme/api",
            tree_paths=["Dockerfile", "src/main.py"],
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.repositories[0].has_dockerfile is True

    async def test_detects_docker_compose(self):
        repo = make_repo(
            "acme/stack",
            tree_paths=["docker-compose.yml"],
        )
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.repositories[0].has_docker_compose is True

    async def test_no_container_when_absent(self):
        repo = make_repo("acme/lambda-fn", tree_paths=["handler.py"])
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        r = result.repositories[0]
        assert r.has_dockerfile is False
        assert r.has_docker_compose is False


# ------------------------------------------------------------------ Error resilience


class TestErrorResilience:
    async def test_single_repo_error_does_not_abort_scan(self):
        good_repo = make_repo("acme/good", tree_paths=["main.py"])
        bad_repo = MagicMock()
        bad_repo.full_name = "acme/bad"

        tree = MagicMock()
        tree.tree = []
        bad_repo.get_git_tree.side_effect = Exception("network timeout")
        bad_repo.default_branch = "main"
        bad_repo.clone_url = "https://github.com/acme/bad.git"
        bad_repo.description = ""
        bad_repo.private = False

        org = make_org([bad_repo, good_repo])
        scanner, gh_mock = _scanner_with_org(org)

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        # good repo still discovered even though bad_repo errored
        assert any("acme/good" in r.id for r in result.repositories)

    async def test_multiple_repos_all_scanned(self):
        repos = [make_repo(f"acme/svc-{i}", tree_paths=["main.py"]) for i in range(5)]
        scanner, gh_mock = _scanner_with_org(make_org(repos))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert len(result.repositories) == 5

    async def test_repo_id_uses_github_dot_com_prefix(self):
        repo = make_repo("acme/payments")
        scanner, gh_mock = _scanner_with_org(make_org([repo]))

        with patch("sherpa.scanners.code.github.scanner.Github", return_value=gh_mock):
            result = await scanner.scan(_config())

        assert result.repositories[0].id == "github.com/acme/payments"

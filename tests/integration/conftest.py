"""Shared fixtures for integration tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from moto import mock_aws

ACCOUNT_ID = "123456789012"
REGION = "us-east-1"


# ------------------------------------------------------------------ AWS fixtures


@pytest.fixture(autouse=False)
def aws_credentials():
    """Fake credentials so moto doesn't try to reach AWS."""
    prev = {}
    keys = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SECURITY_TOKEN",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
    ]
    for k in keys:
        prev[k] = os.environ.get(k)
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = REGION
    yield
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=False)
def aws_mock(aws_credentials):
    """Activate moto mock for all AWS services used by Sherpa."""
    with mock_aws():
        yield


# ------------------------------------------------------------------ GitHub fixtures


def make_git_tree_item(path: str) -> MagicMock:
    item = MagicMock()
    item.path = path
    return item


def make_file_content(content_bytes: bytes, name: str = "") -> MagicMock:
    f = MagicMock()
    f.decoded_content = content_bytes
    f.name = name
    return f


def make_repo(
    full_name: str,
    default_branch: str = "main",
    tree_paths: list[str] | None = None,
    file_contents: dict[str, bytes] | None = None,
    description: str = "",
    private: bool = False,
    clone_url: str | None = None,
) -> MagicMock:
    """Build a minimal PyGithub Repository mock."""
    repo = MagicMock()
    repo.full_name = full_name
    repo.default_branch = default_branch
    repo.description = description
    repo.private = private
    repo.clone_url = clone_url or f"https://github.com/{full_name}.git"

    # git tree
    tree_mock = MagicMock()
    tree_mock.tree = [make_git_tree_item(p) for p in (tree_paths or [])]
    repo.get_git_tree.return_value = tree_mock

    # file contents
    def _get_contents(path, ref=None):
        if file_contents and path in file_contents:
            return make_file_content(file_contents[path], name=path.rsplit("/", 1)[-1])
        from github import GithubException

        raise GithubException(404, "Not Found", None)

    repo.get_contents.side_effect = _get_contents
    return repo


def make_org(repos: list[MagicMock]) -> MagicMock:
    org = MagicMock()
    org.get_repos.return_value = repos
    return org

"""A small, fixed 'acquired company' served through fake AWS and GitHub clients.

`shuffle_seed=None` returns every API response in a fixed order. Any integer seed scrambles
everything a real API may legitimately return in a different order: list items, page
boundaries, tag order, dict key order, repositories, git trees and directory listings.
Sherpa's output must not change either way (issue #10).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from github import GithubException

ACCOUNT = "123456789012"
REGIONS = ["us-east-1", "eu-west-1"]
ORG = "acme"

_A = f"arn:aws:{{svc}}:{{region}}:{ACCOUNT}"
_USE1 = "us-east-1"
_EUW1 = "eu-west-1"

_PAYMENTS_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

# Paginated operations: (service, region) -> operation -> items. The page key per operation
# is in _PAGE_KEYS. Missing combinations return an empty page.
_PAGINATED: dict[tuple[str, str], dict[str, list[Any]]] = {
    ("ec2", _USE1): {
        "describe_instances": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-0aaa1111",
                        "InstanceType": "t3.small",
                        "State": {"Name": "running"},
                        "VpcId": "vpc-0main",
                        "Tags": [
                            {"Key": "Name", "Value": "payments-api"},
                            {"Key": "Application", "Value": "payments"},
                            {"Key": "env", "Value": "prod"},
                        ],
                        "SecurityGroups": [{"GroupId": "sg-0db"}, {"GroupId": "sg-0web"}],
                    },
                    {
                        "InstanceId": "i-0bbb2222",
                        "InstanceType": "t3.micro",
                        "State": {"Name": "stopped"},
                        "VpcId": "vpc-0main",
                        "Tags": [{"Key": "Name", "Value": "checkout-web"}],
                        "SecurityGroups": [{"GroupId": "sg-0web"}],
                    },
                ]
            }
        ],
        "describe_security_groups": [
            {
                "GroupId": "sg-0web",
                "GroupName": "checkout-web-sg",
                "VpcId": "vpc-0main",
                "Description": "web",
            },
            {
                "GroupId": "sg-0db",
                "GroupName": "payments-db-sg",
                "VpcId": "vpc-0main",
                "Description": "db",
                "Tags": [{"Key": "Application", "Value": "payments"}],
            },
        ],
        "describe_vpcs": [
            {
                "VpcId": "vpc-0main",
                "CidrBlock": "10.0.0.0/16",
                "IsDefault": False,
                "Tags": [{"Key": "Name", "Value": "shared-main"}],
            }
        ],
    },
    ("ec2", _EUW1): {
        "describe_instances": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-0ccc3333",
                        "InstanceType": "m5.large",
                        "State": {"Name": "running"},
                        "VpcId": "vpc-0eu",
                        "Tags": [
                            {"Key": "Name", "Value": "reporting-batch"},
                            {"Key": "Team", "Value": "reporting"},
                        ],
                        "SecurityGroups": [],
                    }
                ]
            }
        ],
    },
    ("lambda", _USE1): {
        "list_functions": [
            {
                "FunctionName": "payments-processor",
                "FunctionArn": f"arn:aws:lambda:{_USE1}:{ACCOUNT}:function:payments-processor",
                "Runtime": "python3.12",
                "Handler": "app.handler",
                "Role": f"arn:aws:iam::{ACCOUNT}:role/payments-lambda-role",
                "MemorySize": 512,
            }
        ],
    },
    ("rds", _USE1): {
        "describe_db_instances": [
            {
                "DBInstanceIdentifier": "payments-db",
                "DBInstanceArn": f"arn:aws:rds:{_USE1}:{ACCOUNT}:db:payments-db",
                "Engine": "postgres",
                "EngineVersion": "15.4",
                "DBInstanceClass": "db.t3.medium",
                "MultiAZ": True,
                "TagList": [
                    {"Key": "Application", "Value": "payments"},
                    {"Key": "DataClass", "Value": "pii"},
                ],
            }
        ],
    },
    ("dynamodb", _EUW1): {"list_tables": ["reporting-events"]},
    ("sqs", _USE1): {
        "list_queues": [f"https://sqs.{_USE1}.amazonaws.com/{ACCOUNT}/payments-queue"],
    },
    ("sns", _USE1): {
        "list_topics": [{"TopicArn": f"arn:aws:sns:{_USE1}:{ACCOUNT}:checkout-events"}],
    },
    ("ecs", _USE1): {
        "list_clusters": [f"arn:aws:ecs:{_USE1}:{ACCOUNT}:cluster/checkout-cluster"],
    },
    ("iam", _USE1): {
        "list_roles": [
            {
                "RoleName": "payments-lambda-role",
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/payments-lambda-role",
                "Path": "/",
                "AssumeRolePolicyDocument": _PAYMENTS_TRUST_POLICY,
            },
            {
                "RoleName": "ci-deploy-role",
                "Arn": f"arn:aws:iam::{ACCOUNT}:role/ci-deploy-role",
                "Path": "/ci/",
                "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": []},
            },
        ],
    },
}

_PAGE_KEYS = {
    "describe_instances": "Reservations",
    "describe_security_groups": "SecurityGroups",
    "describe_vpcs": "Vpcs",
    "list_functions": "Functions",
    "describe_db_instances": "DBInstances",
    "list_tables": "TableNames",
    "list_queues": "QueueUrls",
    "list_topics": "Topics",
    "list_clusters": "clusterArns",
    "list_roles": "Roles",
}

_BUCKETS = {
    "payments-receipts": None,  # us-east-1 reports no LocationConstraint
    "reporting-exports": _EUW1,
    "checkout-assets": _USE1,
}

_TABLES = {
    "reporting-events": {
        "TableArn": f"arn:aws:dynamodb:{_EUW1}:{ACCOUNT}:table/reporting-events",
        "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
        "ItemCount": 1200,
        "TableSizeBytes": 524288,
    }
}

_CLUSTERS = {
    f"arn:aws:ecs:{_USE1}:{ACCOUNT}:cluster/checkout-cluster": {
        "clusterArn": f"arn:aws:ecs:{_USE1}:{ACCOUNT}:cluster/checkout-cluster",
        "clusterName": "checkout-cluster",
        "status": "ACTIVE",
        "runningTasksCount": 3,
        "tags": [{"key": "Application", "value": "checkout"}, {"key": "env", "value": "prod"}],
    }
}

_EVENT_SOURCES = {
    f"arn:aws:lambda:{_USE1}:{ACCOUNT}:function:payments-processor": [
        {"EventSourceArn": f"arn:aws:sqs:{_USE1}:{ACCOUNT}:payments-queue"}
    ]
}

# Lists whose order carries no meaning in AWS responses; everything else keeps its order.
_UNORDERED_KEYS = {
    "Reservations",
    "Instances",
    "SecurityGroups",
    "Vpcs",
    "Functions",
    "DBInstances",
    "TableNames",
    "QueueUrls",
    "Topics",
    "clusterArns",
    "Roles",
    "Tags",
    "TagList",
    "tags",
    "Buckets",
    "EventSourceMappings",
    "clusters",
}


class _Shuffler:
    def __init__(self, seed: int | None) -> None:
        self._rng = random.Random(seed) if seed is not None else None

    def list(self, items: list[Any]) -> list[Any]:
        items = list(items)
        if self._rng:
            self._rng.shuffle(items)
        return items

    def deep(self, value: Any, key: str | None = None) -> Any:
        """Copy `value`, scrambling dict key order everywhere and unordered lists."""
        if isinstance(value, dict):
            keys = self.list(list(value))
            return {k: self.deep(value[k], k) for k in keys}
        if isinstance(value, list):
            copied = [self.deep(v) for v in value]
            return self.list(copied) if key in _UNORDERED_KEYS else copied
        return value

    def pages(self, items: list[Any]) -> list[list[Any]]:
        items = self.list(items)
        if not self._rng or len(items) < 2:
            return [items]
        cut = self._rng.randint(1, len(items) - 1)
        return [items[:cut], items[cut:]]


class _AsyncPages:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = iter(pages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._pages)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeAwsClient:
    def __init__(self, service: str, region: str, shuffler: _Shuffler) -> None:
        self._service, self._region, self._s = service, region, shuffler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_paginator(self, operation: str):
        key = _PAGE_KEYS[operation]
        items = _PAGINATED.get((self._service, self._region), {}).get(operation, [])
        pages = [self._s.deep({key: page}) for page in self._s.pages(items)]
        paginator = MagicMock()
        paginator.paginate.side_effect = lambda **_kw: _AsyncPages(pages)
        return paginator

    async def get_caller_identity(self):
        return {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/sherpa-scanner"}

    async def list_buckets(self):
        created = datetime(2024, 1, 15, tzinfo=UTC)
        buckets = [{"Name": n, "CreationDate": created} for n in _BUCKETS]
        return self._s.deep({"Buckets": buckets})

    async def get_bucket_location(self, Bucket: str):  # noqa: N803 (AWS parameter name)
        return {"LocationConstraint": _BUCKETS[Bucket]}

    async def describe_table(self, TableName: str):  # noqa: N803
        return self._s.deep({"Table": _TABLES[TableName]})

    async def describe_clusters(self, clusters: list[str]):
        return self._s.deep({"clusters": [_CLUSTERS[a] for a in clusters]})

    async def list_event_source_mappings(self, FunctionName: str):  # noqa: N803
        return self._s.deep({"EventSourceMappings": _EVENT_SOURCES.get(FunctionName, [])})


class FakeAwsSession:
    def __init__(self, shuffle_seed: int | None = None) -> None:
        self._shuffler = _Shuffler(shuffle_seed)

    def client(self, service: str, region_name: str = _USE1, **_kw):
        return _FakeAwsClient(service, region_name, self._shuffler)


# ------------------------------------------------------------------ GitHub

_WORKFLOW_DEPLOY = f"""name: deploy
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
  deploy:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::{ACCOUNT}:role/ci-deploy-role
          aws-region: us-east-1
      - run: terraform apply -auto-approve
"""

_REPOS: dict[str, dict[str, bytes]] = {
    f"{ORG}/payments-service": {
        "main.tf": (
            f'data "aws_db_instance" "db" {{ arn = "arn:aws:rds:{_USE1}:{ACCOUNT}:db:payments-db" }}\n'
            f'locals {{ queue = "arn:aws:sqs:{_USE1}:{ACCOUNT}:payments-queue" }}\n'
        ).encode(),
        "requirements.txt": b"boto3>=1.35\nfastapi==0.115.0\n# comment\npydantic>=2\n",
        "Dockerfile": b"FROM python:3.12-slim\n",
        ".github/workflows/deploy.yml": _WORKFLOW_DEPLOY.encode(),
    },
    f"{ORG}/checkout-web": {
        "package.json": b'{"dependencies": {"react": "^18.3.0", "axios": "^1.7.0"},'
        b' "devDependencies": {"vite": "^5.0.0"}}',
        "docker-compose.yml": b"services: {}\n",
    },
    f"{ORG}/handbook": {"README.md": b"# Handbook\n"},
}


def _file(path: str, content: bytes) -> MagicMock:
    f = MagicMock()
    f.name = path.rsplit("/", 1)[-1]
    f.path = path
    f.decoded_content = content
    return f


def _repo(full_name: str, files: dict[str, bytes], s: _Shuffler) -> MagicMock:
    repo = MagicMock()
    repo.full_name = full_name
    repo.default_branch = "main"
    repo.description = ""
    repo.private = True
    repo.clone_url = f"https://github.com/{full_name}.git"

    tree = MagicMock()
    tree.tree = [MagicMock(path=p) for p in s.list(list(files))]
    repo.get_git_tree.return_value = tree

    def get_contents(path: str, ref: str | None = None):
        if path in files:
            return _file(path, files[path])
        children = [p for p in files if p.startswith(path.rstrip("/") + "/")]
        if children:
            return [_file(p, files[p]) for p in s.list(children)]
        raise GithubException(404, "Not Found", None)

    repo.get_contents.side_effect = get_contents
    return repo


def fake_github_client(shuffle_seed: int | None = None) -> MagicMock:
    """A PyGithub `Github` instance stand-in serving the fixed org."""
    s = _Shuffler(shuffle_seed)
    org = MagicMock()
    org.get_repos.return_value = [_repo(n, f, s) for n, f in s.list(list(_REPOS.items()))]
    gh = MagicMock()
    gh.get_organization.return_value = org
    return gh

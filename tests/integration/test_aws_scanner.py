"""Integration tests for AWS collector functions and AwsCloudScanner.

moto does not patch aiobotocore's async HTTP layer, so we test the collector
functions directly by passing AsyncMock clients that return realistic AWS
response shapes. This exercises the resource mapping, dependency detection,
and deduplication logic — the parts that matter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from sherpa.core.models import ScanConfig
from sherpa.core.models.enums import DependencyType, ResourceType
from sherpa.scanners.cloud.aws.collector import (
    collect_dynamodb,
    collect_ec2,
    collect_ecs_clusters,
    collect_iam_roles,
    collect_lambda,
    collect_rds,
    collect_s3,
    collect_sns,
    collect_sqs,
)
from sherpa.scanners.cloud.aws.scanner import AwsCloudScanner

ACCOUNT = "123456789012"
REGION = "us-east-1"


# ------------------------------------------------------------------ helpers


class _aiter:
    """Minimal async iterator for paginator mocking."""

    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


def _async_paginator(pages: list[dict]) -> MagicMock:
    """Build a mock paginator whose .paginate() is an async iterator."""
    pag = MagicMock()
    pag.paginate.return_value = _aiter(pages)
    return pag


def _client(paginator: MagicMock | None = None, **async_methods) -> MagicMock:
    """Build a mock boto3 client.

    get_paginator() is synchronous in aioboto3 so it must be a plain MagicMock.
    Other methods (list_buckets, list_event_source_mappings, …) are coroutines
    in aioboto3, so they must be AsyncMocks.
    """
    c = MagicMock()
    if paginator is not None:
        c.get_paginator.return_value = paginator
    for name, return_value in async_methods.items():
        am = AsyncMock(return_value=return_value)
        setattr(c, name, am)
    return c


# ------------------------------------------------------------------ EC2


class TestCollectEC2:
    async def test_maps_instance_tags_and_metadata(self):
        client = _client(
            _async_paginator(
                [
                    {
                        "Reservations": [
                            {
                                "Instances": [
                                    {
                                        "InstanceId": "i-abc123",
                                        "InstanceType": "t3.medium",
                                        "State": {"Name": "running"},
                                        "VpcId": "vpc-111",
                                        "SecurityGroups": [
                                            {"GroupId": "sg-aaa", "GroupName": "app-sg"}
                                        ],
                                        "Tags": [
                                            {"Key": "Name", "Value": "checkout-api"},
                                            {"Key": "Application", "Value": "checkout"},
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            )
        )

        resources = await collect_ec2(client, REGION, ACCOUNT)

        assert len(resources) == 1
        r = resources[0]
        assert r.resource_type == ResourceType.EC2_INSTANCE
        assert r.name == "checkout-api"
        assert r.tags["Application"] == "checkout"
        assert r.metadata["instance_type"] == "t3.medium"
        assert r.metadata["state"] == "running"
        assert "i-abc123" in r.id

    async def test_security_group_dependency_emitted(self):
        client = _client(
            _async_paginator(
                [
                    {
                        "Reservations": [
                            {
                                "Instances": [
                                    {
                                        "InstanceId": "i-abc123",
                                        "InstanceType": "t2.micro",
                                        "State": {"Name": "running"},
                                        "SecurityGroups": [
                                            {"GroupId": "sg-111"},
                                            {"GroupId": "sg-222"},
                                        ],
                                        "Tags": [],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            )
        )

        resources = await collect_ec2(client, REGION, ACCOUNT)

        deps = resources[0].dependencies
        assert len(deps) == 2
        assert all(d.dependency_type == DependencyType.NETWORK for d in deps)
        dep_targets = {d.target_id for d in deps}
        assert any("sg-111" in t for t in dep_targets)
        assert any("sg-222" in t for t in dep_targets)

    async def test_empty_reservations_returns_empty_list(self):
        client = _client(_async_paginator([{"Reservations": []}]))
        resources = await collect_ec2(client, REGION, ACCOUNT)
        assert resources == []

    async def test_multiple_pages_aggregated(self):
        client = _client(
            _async_paginator(
                [
                    {
                        "Reservations": [
                            {
                                "Instances": [
                                    {
                                        "InstanceId": "i-aaa",
                                        "InstanceType": "t2.micro",
                                        "State": {"Name": "running"},
                                        "SecurityGroups": [],
                                        "Tags": [],
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "Reservations": [
                            {
                                "Instances": [
                                    {
                                        "InstanceId": "i-bbb",
                                        "InstanceType": "t2.micro",
                                        "State": {"Name": "stopped"},
                                        "SecurityGroups": [],
                                        "Tags": [],
                                    }
                                ]
                            }
                        ]
                    },
                ]
            )
        )
        resources = await collect_ec2(client, REGION, ACCOUNT)
        assert len(resources) == 2


# ------------------------------------------------------------------ Lambda


class TestCollectLambda:
    async def test_maps_function_metadata(self):
        client = _client(
            _async_paginator(
                [
                    {
                        "Functions": [
                            {
                                "FunctionName": "payments-processor",
                                "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function/payments-processor",
                                "Runtime": "python3.12",
                                "Handler": "handler.main",
                                "Role": f"arn:aws:iam::{ACCOUNT}:role/lambda-role",
                                "MemorySize": 256,
                            }
                        ]
                    }
                ]
            ),
            list_event_source_mappings={"EventSourceMappings": []},
        )

        resources = await collect_lambda(client, REGION, ACCOUNT)

        assert len(resources) == 1
        assert resources[0].name == "payments-processor"
        assert resources[0].metadata["runtime"] == "python3.12"
        assert resources[0].metadata["memory_size"] == 256

    async def test_sqs_event_source_mapping_emits_dependency(self):
        fn_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function/consumer"
        queue_arn = f"arn:aws:sqs:{REGION}:{ACCOUNT}:queue/jobs"
        client = _client(
            _async_paginator(
                [
                    {
                        "Functions": [
                            {
                                "FunctionName": "consumer",
                                "FunctionArn": fn_arn,
                                "Runtime": "python3.12",
                                "Handler": "h.main",
                                "Role": "arn:aws:iam::123:role/r",
                                "MemorySize": 128,
                            }
                        ]
                    }
                ]
            ),
            list_event_source_mappings={"EventSourceMappings": [{"EventSourceArn": queue_arn}]},
        )

        resources = await collect_lambda(client, REGION, ACCOUNT)

        deps = resources[0].dependencies
        assert len(deps) == 1
        assert deps[0].dependency_type == DependencyType.READS_FROM
        assert deps[0].target_id == queue_arn

    async def test_esm_error_does_not_abort_collection(self):
        fn_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function/fn"
        client = _client(
            _async_paginator(
                [
                    {
                        "Functions": [
                            {
                                "FunctionName": "fn",
                                "FunctionArn": fn_arn,
                                "Runtime": "python3.12",
                                "Handler": "h.main",
                                "Role": "arn:aws:iam::123:role/r",
                                "MemorySize": 128,
                            }
                        ]
                    }
                ]
            ),
        )
        client.list_event_source_mappings = AsyncMock(side_effect=Exception("AccessDenied"))

        resources = await collect_lambda(client, REGION, ACCOUNT)

        # function still collected despite ESM error, just no dep edges
        assert len(resources) == 1
        assert resources[0].dependencies == []


# ------------------------------------------------------------------ Storage


class TestCollectS3:
    async def test_discovers_bucket_in_home_region(self):
        client = _client(
            list_buckets={"Buckets": [{"Name": "acme-data", "CreationDate": "2024-01-01"}]},
            get_bucket_location={"LocationConstraint": REGION},
        )

        resources = await collect_s3(client, REGION, ACCOUNT)

        assert len(resources) == 1
        assert resources[0].name == "acme-data"
        assert resources[0].resource_type == ResourceType.S3_BUCKET

    async def test_bucket_in_different_region_excluded(self):
        client = _client(
            list_buckets={"Buckets": [{"Name": "west-bucket", "CreationDate": "2024-01-01"}]},
            get_bucket_location={"LocationConstraint": "us-west-2"},
        )

        resources = await collect_s3(client, REGION, ACCOUNT)

        assert resources == []

    async def test_us_east_1_bucket_location_null_treated_as_home(self):
        client = _client(
            list_buckets={"Buckets": [{"Name": "legacy", "CreationDate": "2024-01-01"}]},
            get_bucket_location={"LocationConstraint": None},
        )

        resources = await collect_s3(client, REGION, ACCOUNT)

        assert len(resources) == 1


class TestCollectDynamoDB:
    async def test_maps_table_metadata(self):
        client = _client(
            _async_paginator([{"TableNames": ["orders"]}]),
            describe_table={
                "Table": {
                    "TableName": "orders",
                    "TableArn": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders",
                    "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
                    "ItemCount": 5000,
                    "TableSizeBytes": 1024000,
                }
            },
        )

        resources = await collect_dynamodb(client, REGION, ACCOUNT)

        assert len(resources) == 1
        assert resources[0].name == "orders"
        assert resources[0].metadata["billing_mode"] == "PAY_PER_REQUEST"
        assert resources[0].metadata["item_count"] == 5000


class TestCollectRDS:
    async def test_maps_instance_metadata(self):
        client = _client(
            _async_paginator(
                [
                    {
                        "DBInstances": [
                            {
                                "DBInstanceIdentifier": "payments-db",
                                "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:payments-db",
                                "Engine": "postgres",
                                "EngineVersion": "15.4",
                                "DBInstanceClass": "db.t3.medium",
                                "MultiAZ": True,
                                "TagList": [{"Key": "Env", "Value": "prod"}],
                            }
                        ]
                    }
                ]
            )
        )

        resources = await collect_rds(client, REGION, ACCOUNT)

        assert len(resources) == 1
        r = resources[0]
        assert r.name == "payments-db"
        assert r.metadata["engine"] == "postgres"
        assert r.metadata["multi_az"] is True
        assert r.tags["Env"] == "prod"


# ------------------------------------------------------------------ Messaging


class TestCollectSQS:
    async def test_extracts_queue_name_from_url(self):
        client = _client(
            _async_paginator(
                [{"QueueUrls": [f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/events-queue"]}]
            )
        )

        resources = await collect_sqs(client, REGION, ACCOUNT)

        assert len(resources) == 1
        assert resources[0].name == "events-queue"
        assert resources[0].resource_type == ResourceType.SQS_QUEUE

    async def test_empty_page_returns_empty_list(self):
        client = _client(_async_paginator([{}]))
        resources = await collect_sqs(client, REGION, ACCOUNT)
        assert resources == []


class TestCollectSNS:
    async def test_extracts_topic_name_from_arn(self):
        topic_arn = f"arn:aws:sns:{REGION}:{ACCOUNT}:user-events"
        client = _client(_async_paginator([{"Topics": [{"TopicArn": topic_arn}]}]))

        resources = await collect_sns(client, REGION, ACCOUNT)

        assert len(resources) == 1
        assert resources[0].name == "user-events"
        assert resources[0].id == topic_arn


# ------------------------------------------------------------------ Compute (ECS, IAM)


class TestCollectECS:
    async def test_maps_cluster_metadata(self):
        cluster_arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/prod-cluster"
        client = _client(
            _async_paginator([{"clusterArns": [cluster_arn]}]),
            describe_clusters={
                "clusters": [
                    {
                        "clusterArn": cluster_arn,
                        "clusterName": "prod-cluster",
                        "status": "ACTIVE",
                        "runningTasksCount": 12,
                        "tags": [{"key": "Env", "value": "prod"}],
                    }
                ]
            },
        )

        resources = await collect_ecs_clusters(client, REGION, ACCOUNT)

        assert len(resources) == 1
        assert resources[0].name == "prod-cluster"
        assert resources[0].metadata["running_tasks_count"] == 12
        assert resources[0].tags["Env"] == "prod"

    async def test_empty_cluster_page_returns_empty_list(self):
        client = _client(_async_paginator([{"clusterArns": []}]))
        resources = await collect_ecs_clusters(client, REGION, ACCOUNT)
        assert resources == []


class TestCollectIAM:
    async def test_maps_role_with_global_region(self):
        client = _client(
            _async_paginator(
                [
                    {
                        "Roles": [
                            {
                                "RoleName": "app-role",
                                "Arn": f"arn:aws:iam::{ACCOUNT}:role/app-role",
                                "Path": "/",
                                "AssumeRolePolicyDocument": {},
                            }
                        ]
                    }
                ]
            )
        )

        resources = await collect_iam_roles(client, REGION, ACCOUNT)

        assert len(resources) == 1
        assert resources[0].name == "app-role"
        assert resources[0].region == "global"  # IAM is global


# ------------------------------------------------------------------ Full scanner


class TestAwsCloudScanner:
    async def test_validate_config_fails_without_accounts(self):
        scanner = AwsCloudScanner()
        config = ScanConfig(github_org="acme")
        result = await scanner.validate_config(config)
        assert not result.valid
        assert any("aws_accounts" in e for e in result.errors)

    async def test_validate_config_fails_without_regions(self):
        scanner = AwsCloudScanner()
        config = ScanConfig(aws_accounts=["123456789012"], aws_regions=[])
        result = await scanner.validate_config(config)
        assert not result.valid

    async def test_validate_config_passes_with_accounts_and_regions(self):
        scanner = AwsCloudScanner()
        config = ScanConfig(aws_accounts=["123456789012"], aws_regions=["us-east-1"])
        result = await scanner.validate_config(config)
        assert result.valid

    async def test_collector_exception_recorded_as_error(self):
        scanner = AwsCloudScanner()

        async def _failing_collector(client, region, account):
            raise PermissionError("AccessDenied")

        with patch.object(
            scanner,
            "_run_collector",
            side_effect=PermissionError("AccessDenied"),
        ):
            config = ScanConfig(
                aws_accounts=[ACCOUNT], aws_regions=[REGION], service_categories=["compute"]
            )
            result = await scanner.scan(config)

        assert len(result.errors) > 0
        assert len(result.coverage_gaps) > 0

    async def test_results_are_deduplicated_by_id(self):
        scanner = AwsCloudScanner()
        from sherpa.core.models import Resource

        r = Resource(
            id="arn:aws:sqs:us-east-1:123:queue/q",
            resource_type=ResourceType.SQS_QUEUE,
            region=REGION,
            account_id=ACCOUNT,
            name="q",
        )

        async def _dup_collector(*args, **kwargs):
            return [r, r]

        with patch.object(scanner, "_run_collector", side_effect=_dup_collector):
            config = ScanConfig(
                aws_accounts=[ACCOUNT], aws_regions=[REGION], service_categories=["messaging"]
            )
            result = await scanner.scan(config)

        assert len(result.resources) == 1

    async def test_output_sorted_by_id(self):
        scanner = AwsCloudScanner()
        from sherpa.core.models import Resource

        resources = [
            Resource(
                id=f"arn:aws:sqs:{REGION}:{ACCOUNT}:queue/{n}",
                resource_type=ResourceType.SQS_QUEUE,
                region=REGION,
                account_id=ACCOUNT,
                name=n,
            )
            for n in ("charlie", "alpha", "bravo")
        ]

        async def _collector(*args, **kwargs):
            return resources

        with patch.object(scanner, "_run_collector", side_effect=_collector):
            config = ScanConfig(
                aws_accounts=[ACCOUNT], aws_regions=[REGION], service_categories=["messaging"]
            )
            result = await scanner.scan(config)

        ids = [r.id for r in result.resources]
        assert ids == sorted(ids)

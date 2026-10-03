"""Per-service resource collectors. Each returns a list of Resource objects."""

from __future__ import annotations

import json
from typing import Any

from sherpa.core.models import CoverageGap, Resource, ResourceDependency
from sherpa.core.models.enums import DependencyPlane, DependencyType, ResourceType

from .errors import classify


def _arn(service: str, resource_type: str, region: str, account: str, resource_id: str) -> str:
    return f"arn:aws:{service}:{region}:{account}:{resource_type}/{resource_id}"


def _report(
    issues: list[CoverageGap] | None,
    *,
    description: str,
    account_id: str,
    service: str,
    failures: list[Exception],
    region: str | None = None,
) -> None:
    """Record partial failures inside a collector as one coverage gap (never silently)."""
    if not failures or issues is None:
        return
    issues.append(
        CoverageGap(
            description=description,
            affected_regions=[region] if region else [],
            affected_services=[service],
            severity="warning",
            scanner="aws-cloud",
            scope=f"aws-account:{account_id}",
            error_class=classify(failures[0]),
        )
    )


def _policy_text(doc: Any) -> str:
    """Stable text for a policy document (boto3 returns a dict; key order varies)."""
    return doc if isinstance(doc, str) else json.dumps(doc, sort_keys=True)


async def collect_ec2(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("describe_instances")
    async for page in paginator.paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                iid = inst["InstanceId"]
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                sg_deps = [
                    ResourceDependency(
                        source_id=_arn("ec2", "instance", region, account_id, iid),
                        target_id=_arn("ec2", "security-group", region, account_id, sg["GroupId"]),
                        dependency_type=DependencyType.NETWORK,
                        plane=DependencyPlane.CLOUD,
                    )
                    for sg in inst.get("SecurityGroups", [])
                ]
                resources.append(
                    Resource(
                        id=_arn("ec2", "instance", region, account_id, iid),
                        resource_type=ResourceType.EC2_INSTANCE,
                        region=region,
                        account_id=account_id,
                        name=tags.get("Name", iid),
                        tags=tags,
                        metadata={
                            "instance_type": inst.get("InstanceType"),
                            "state": inst.get("State", {}).get("Name"),
                            "vpc_id": inst.get("VpcId"),
                        },
                        dependencies=sg_deps,
                    )
                )
    return resources


async def collect_security_groups(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("describe_security_groups")
    async for page in paginator.paginate():
        for sg in page["SecurityGroups"]:
            tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
            resources.append(
                Resource(
                    id=_arn("ec2", "security-group", region, account_id, sg["GroupId"]),
                    resource_type=ResourceType.EC2_SECURITY_GROUP,
                    region=region,
                    account_id=account_id,
                    name=sg.get("GroupName", sg["GroupId"]),
                    tags=tags,
                    metadata={
                        "vpc_id": sg.get("VpcId"),
                        "description": sg.get("Description"),
                    },
                )
            )
    return resources


async def collect_vpcs(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("describe_vpcs")
    async for page in paginator.paginate():
        for vpc in page["Vpcs"]:
            tags = {t["Key"]: t["Value"] for t in vpc.get("Tags", [])}
            resources.append(
                Resource(
                    id=_arn("ec2", "vpc", region, account_id, vpc["VpcId"]),
                    resource_type=ResourceType.EC2_VPC,
                    region=region,
                    account_id=account_id,
                    name=tags.get("Name", vpc["VpcId"]),
                    tags=tags,
                    metadata={"cidr": vpc.get("CidrBlock"), "is_default": vpc.get("IsDefault")},
                )
            )
    return resources


async def collect_lambda(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    esm_failures: list[Exception] = []
    paginator = client.get_paginator("list_functions")
    async for page in paginator.paginate():
        for fn in page["Functions"]:
            fn_arn = fn["FunctionArn"]
            # Collect event source mappings for dependency edges
            esm_deps: list[ResourceDependency] = []
            try:
                esm_resp = await client.list_event_source_mappings(FunctionName=fn_arn)
                for esm in esm_resp.get("EventSourceMappings", []):
                    source_arn = esm.get("EventSourceArn", "")
                    if source_arn:
                        esm_deps.append(
                            ResourceDependency(
                                source_id=fn_arn,
                                target_id=source_arn,
                                dependency_type=DependencyType.READS_FROM,
                                plane=DependencyPlane.CLOUD,
                            )
                        )
            except Exception as exc:  # noqa: BLE001 - reported as a coverage gap below
                esm_failures.append(exc)
            resources.append(
                Resource(
                    id=fn_arn,
                    resource_type=ResourceType.LAMBDA_FUNCTION,
                    region=region,
                    account_id=account_id,
                    name=fn["FunctionName"],
                    tags={},
                    metadata={
                        "runtime": fn.get("Runtime"),
                        "handler": fn.get("Handler"),
                        "role": fn.get("Role"),
                        "memory_size": fn.get("MemorySize"),
                    },
                    dependencies=esm_deps,
                )
            )
    _report(
        issues,
        description=(
            f"Event source mappings could not be read for {len(esm_failures)} Lambda "
            "function(s): their event-source dependencies are missing"
        ),
        account_id=account_id,
        service="lambda",
        failures=esm_failures,
        region=region,
    )
    return resources


async def collect_s3(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    location_failures: list[Exception] = []
    resp = await client.list_buckets()
    for bucket in resp.get("Buckets", []):
        name = bucket["Name"]
        try:
            loc = await client.get_bucket_location(Bucket=name)
            bucket_region = loc.get("LocationConstraint") or "us-east-1"
        except Exception as exc:  # noqa: BLE001 - reported as a coverage gap below
            # Never guess: a guessed region would put the bucket in the wrong place.
            location_failures.append(exc)
            bucket_region = "unknown"
        if bucket_region not in (region, "unknown"):
            continue  # only emit buckets for their home region
        resources.append(
            Resource(
                id=f"arn:aws:s3:::{name}",
                resource_type=ResourceType.S3_BUCKET,
                region=bucket_region,
                account_id=account_id,
                name=name,
                tags={},
                metadata={"creation_date": str(bucket.get("CreationDate", ""))},
            )
        )
    # Region-agnostic on purpose: identical across regional calls, so reported once.
    _report(
        issues,
        description=(
            f"The region of {len(location_failures)} S3 bucket(s) could not be read: they "
            "are listed with region 'unknown'"
        ),
        account_id=account_id,
        service="s3",
        failures=location_failures,
    )
    return resources


async def collect_rds(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("describe_db_instances")
    async for page in paginator.paginate():
        for db in page["DBInstances"]:
            arn = db["DBInstanceArn"]
            tags = {t["Key"]: t["Value"] for t in db.get("TagList", [])}
            resources.append(
                Resource(
                    id=arn,
                    resource_type=ResourceType.RDS_INSTANCE,
                    region=region,
                    account_id=account_id,
                    name=db["DBInstanceIdentifier"],
                    tags=tags,
                    metadata={
                        "engine": db.get("Engine"),
                        "engine_version": db.get("EngineVersion"),
                        "instance_class": db.get("DBInstanceClass"),
                        "multi_az": db.get("MultiAZ"),
                    },
                )
            )
    return resources


async def collect_dynamodb(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("list_tables")
    async for page in paginator.paginate():
        for table_name in page.get("TableNames", []):
            desc = await client.describe_table(TableName=table_name)
            table = desc["Table"]
            arn = table["TableArn"]
            resources.append(
                Resource(
                    id=arn,
                    resource_type=ResourceType.DYNAMODB_TABLE,
                    region=region,
                    account_id=account_id,
                    name=table_name,
                    tags={},
                    metadata={
                        "billing_mode": table.get("BillingModeSummary", {}).get("BillingMode"),
                        "item_count": table.get("ItemCount"),
                        "size_bytes": table.get("TableSizeBytes"),
                    },
                )
            )
    return resources


async def collect_sqs(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("list_queues")
    async for page in paginator.paginate():
        for url in page.get("QueueUrls", []):
            queue_name = url.rsplit("/", 1)[-1]
            # Real SQS ARN format has no resource-type segment: arn:aws:sqs:<region>:<acct>:<name>.
            # It must match what other APIs and IaC use (e.g. Lambda event source mappings).
            arn = f"arn:aws:sqs:{region}:{account_id}:{queue_name}"
            resources.append(
                Resource(
                    id=arn,
                    resource_type=ResourceType.SQS_QUEUE,
                    region=region,
                    account_id=account_id,
                    name=queue_name,
                    tags={},
                    metadata={"url": url},
                )
            )
    return resources


async def collect_sns(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("list_topics")
    async for page in paginator.paginate():
        for topic in page.get("Topics", []):
            arn = topic["TopicArn"]
            topic_name = arn.rsplit(":", 1)[-1]
            resources.append(
                Resource(
                    id=arn,
                    resource_type=ResourceType.SNS_TOPIC,
                    region=region,
                    account_id=account_id,
                    name=topic_name,
                    tags={},
                    metadata={},
                )
            )
    return resources


async def collect_ecs_clusters(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("list_clusters")
    async for page in paginator.paginate():
        arns = page.get("clusterArns", [])
        if not arns:
            continue
        desc = await client.describe_clusters(clusters=arns)
        for cluster in desc.get("clusters", []):
            arn = cluster["clusterArn"]
            resources.append(
                Resource(
                    id=arn,
                    resource_type=ResourceType.ECS_CLUSTER,
                    region=region,
                    account_id=account_id,
                    name=cluster["clusterName"],
                    tags={t["key"]: t["value"] for t in cluster.get("tags", [])},
                    metadata={
                        "status": cluster.get("status"),
                        "running_tasks_count": cluster.get("runningTasksCount"),
                    },
                )
            )
    return resources


async def collect_iam_roles(
    client: Any, region: str, account_id: str, issues: list[CoverageGap] | None = None
) -> list[Resource]:
    resources: list[Resource] = []
    paginator = client.get_paginator("list_roles")
    async for page in paginator.paginate():
        for role in page.get("Roles", []):
            resources.append(
                Resource(
                    id=role["Arn"],
                    resource_type=ResourceType.IAM_ROLE,
                    region="global",
                    account_id=account_id,
                    name=role["RoleName"],
                    tags={},
                    metadata={
                        "path": role.get("Path"),
                        "trust_policy": _policy_text(role.get("AssumeRolePolicyDocument", {})),
                    },
                )
            )
    return resources

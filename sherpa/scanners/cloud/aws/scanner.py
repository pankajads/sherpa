from __future__ import annotations

import asyncio
from typing import Any

import aioboto3

from sherpa.core.interfaces import ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import CoverageGap, Resource, ScanConfig

from .collector import (
    collect_dynamodb,
    collect_ec2,
    collect_ecs_clusters,
    collect_iam_roles,
    collect_lambda,
    collect_rds,
    collect_s3,
    collect_security_groups,
    collect_sns,
    collect_sqs,
    collect_vpcs,
)

_CATEGORY_COLLECTORS: dict[str, list[tuple[str, Any]]] = {
    "compute": [
        ("ec2", collect_ec2),
        ("ec2", collect_security_groups),
        ("ec2", collect_vpcs),
        ("lambda", collect_lambda),
        ("ecs", collect_ecs_clusters),
    ],
    "storage": [
        ("s3", collect_s3),
        ("rds", collect_rds),
        ("dynamodb", collect_dynamodb),
    ],
    "messaging": [
        ("sqs", collect_sqs),
        ("sns", collect_sns),
    ],
    "security": [
        ("iam", collect_iam_roles),
    ],
}


class AwsCloudScanner(ScannerPlugin):
    @property
    def scanner_type(self) -> str:
        return "aws-cloud"

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        errors = []
        if not config.aws_accounts:
            errors.append("aws_accounts must not be empty")
        if not config.aws_regions:
            errors.append("aws_regions must not be empty")
        return ValidationResult(valid=not errors, errors=errors)

    async def scan(self, config: ScanConfig) -> ScanResult:
        all_resources: list[Resource] = []
        coverage_gaps: list[CoverageGap] = []
        errors: list[str] = []

        session = aioboto3.Session()

        tasks = []
        for account_id in config.aws_accounts:
            for region in config.aws_regions:
                for category in config.service_categories:
                    collectors = _CATEGORY_COLLECTORS.get(category, [])
                    for service, collector_fn in collectors:
                        tasks.append(
                            self._run_collector(
                                session, collector_fn, service, region, account_id, config
                            )
                        )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
            elif isinstance(result, list):
                all_resources.extend(result)

        # Deduplicate by ID (deterministic: prefer first seen, sorted by id)
        seen: set[str] = set()
        deduped: list[Resource] = []
        for r in sorted(all_resources, key=lambda x: x.id):
            if r.id not in seen:
                seen.add(r.id)
                deduped.append(r)

        if errors:
            coverage_gaps.append(
                CoverageGap(
                    description=f"{len(errors)} collector(s) failed",
                    severity="warning",
                )
            )

        return ScanResult(
            scanner_type=self.scanner_type,
            resources=deduped,
            coverage_gaps=coverage_gaps,
            errors=errors,
        )

    async def _run_collector(
        self,
        session: aioboto3.Session,
        collector_fn: Any,
        service: str,
        region: str,
        account_id: str,
        config: ScanConfig,
    ) -> list[Resource]:
        kwargs: dict[str, Any] = {"region_name": region}
        if config.assume_role_arn:
            sts_client_kwargs: dict[str, Any] = {}
            async with session.client("sts", **sts_client_kwargs) as sts:
                assumed = await sts.assume_role(
                    RoleArn=config.assume_role_arn,
                    RoleSessionName="SherpaDiscovery",
                )
            creds = assumed["Credentials"]
            kwargs.update(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )

        # IAM is global — us-east-1 only to avoid duplicates
        if service == "iam" and region != "us-east-1":
            return []

        async with session.client(service, **kwargs) as client:
            return await collector_fn(client, region, account_id)

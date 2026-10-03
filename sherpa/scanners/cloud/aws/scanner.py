from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aioboto3

from sherpa.core.interfaces import ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import AwsTarget, CoverageGap, Resource, ScanConfig

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


# Stable session name so the target's security team can find every Sherpa call in CloudTrail.
ROLE_SESSION_NAME = "SherpaDiscovery"
_REFRESH_MARGIN = timedelta(minutes=5)
# IAM is global: scan it once per account through its global (us-east-1) endpoint, whatever
# regions the account uses.
_GLOBAL_SERVICES = {"iam": "us-east-1"}


def _arn_account(resource_id: str) -> str | None:
    parts = resource_id.split(":")
    if resource_id.startswith("arn:") and len(parts) > 4 and parts[4].isdigit():
        return parts[4]
    return None


class _AccountCredentials:
    """Credentials for one account: assumed once, cached, refreshed shortly before expiry."""

    def __init__(self, session: Any, target: AwsTarget, now: Callable[[], datetime]) -> None:
        self._session = session
        self._target = target
        self._now = now
        self._lock = asyncio.Lock()
        self._kwargs: dict[str, str] | None = None
        self._expires: datetime | None = None

    async def client_kwargs(self) -> dict[str, str]:
        if self._target.role_arn is None:
            return {}  # current credentials
        async with self._lock:
            if (
                self._kwargs is None
                or self._expires is None
                or (self._expires - self._now() < _REFRESH_MARGIN)
            ):
                await self._assume()
            assert self._kwargs is not None
            return dict(self._kwargs)

    async def _assume(self) -> None:
        params: dict[str, str] = {
            "RoleArn": self._target.role_arn or "",
            "RoleSessionName": ROLE_SESSION_NAME,
        }
        if self._target.external_id:
            params["ExternalId"] = self._target.external_id
        region = self._target.regions[0] if self._target.regions else "us-east-1"
        async with self._session.client("sts", region_name=region) as sts:
            resp = await sts.assume_role(**params)
        creds = resp["Credentials"]
        self._kwargs = {
            "aws_access_key_id": creds["AccessKeyId"],
            "aws_secret_access_key": creds["SecretAccessKey"],
            "aws_session_token": creds["SessionToken"],
        }
        self._expires = creds.get("Expiration") or self._now() + timedelta(hours=1)


class AwsCloudScanner(ScannerPlugin):
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def scanner_type(self) -> str:
        return "aws-cloud"

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        errors = []
        if not config.aws_accounts:
            errors.append("aws_accounts must not be empty")
        for target in config.aws_targets():
            if not target.regions:
                errors.append(f"no regions configured for AWS account {target.account_id}")
        return ValidationResult(valid=not errors, errors=errors)

    async def scan(self, config: ScanConfig) -> ScanResult:
        all_resources: list[Resource] = []
        coverage_gaps: list[CoverageGap] = []
        errors: list[str] = []

        session = aioboto3.Session()
        targets = config.aws_targets()
        accounts = {t.account_id: _AccountCredentials(session, t, self._now) for t in targets}

        # Assume each account's role once, up front: one clear error per unreachable account
        # instead of one per collector.
        reachable: list[AwsTarget] = []
        assumed = await asyncio.gather(
            *(accounts[t.account_id].client_kwargs() for t in targets), return_exceptions=True
        )
        for target, result in zip(targets, assumed, strict=True):
            if isinstance(result, BaseException):
                errors.append(f"{target.account_id}: cannot assume {target.role_arn}: {result}")
                coverage_gaps.append(
                    CoverageGap(
                        description=(
                            f"Account {target.account_id} was not scanned: role assumption failed"
                        ),
                        affected_regions=target.regions,
                        severity="error",
                    )
                )
            else:
                reachable.append(target)

        jobs: list[tuple[str, str, str]] = []
        tasks = []
        for target in reachable:
            creds = accounts[target.account_id]
            for category in config.service_categories:
                for service, collector_fn in _CATEGORY_COLLECTORS.get(category, []):
                    regions = (
                        [_GLOBAL_SERVICES[service]]
                        if service in _GLOBAL_SERVICES
                        else target.regions
                    )
                    for region in regions:
                        jobs.append((target.account_id, region, service))
                        tasks.append(
                            self._run_collector(
                                session, creds, collector_fn, service, region, target.account_id
                            )
                        )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed_collectors = 0
        for (account_id, region, service), result in zip(jobs, results, strict=True):
            if isinstance(result, BaseException):
                failed_collectors += 1
                errors.append(f"{account_id}/{region}/{service}: {result}")
            elif isinstance(result, list):
                all_resources.extend(result)

        all_resources, owner_gaps = _attribute_to_owner(all_resources)
        coverage_gaps.extend(owner_gaps)

        # Deduplicate by ID (deterministic: prefer first seen, sorted by id)
        seen: set[str] = set()
        deduped: list[Resource] = []
        for r in sorted(all_resources, key=lambda x: x.id):
            if r.id not in seen:
                seen.add(r.id)
                deduped.append(r)

        if failed_collectors:
            coverage_gaps.append(
                CoverageGap(
                    description=f"{failed_collectors} collector(s) failed", severity="warning"
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
        creds: _AccountCredentials,
        collector_fn: Any,
        service: str,
        region: str,
        account_id: str,
    ) -> list[Resource]:
        kwargs: dict[str, Any] = {"region_name": region, **await creds.client_kwargs()}
        async with session.client(service, **kwargs) as client:
            return await collector_fn(client, region, account_id)


def _attribute_to_owner(resources: list[Resource]) -> tuple[list[Resource], list[CoverageGap]]:
    """Label each resource with the account in its ARN, not the account we asked.

    A mismatch means the credentials used for one account returned another account's
    resources (a wrong role mapping), or the resource is shared into the account.
    """
    fixed: list[Resource] = []
    mismatches: dict[tuple[str, str], int] = {}
    for r in resources:
        owner = _arn_account(r.id)
        if owner and owner != r.account_id:
            mismatches[(r.account_id, owner)] = mismatches.get((r.account_id, owner), 0) + 1
            r = r.model_copy(update={"account_id": owner})
        fixed.append(r)
    gaps = [
        CoverageGap(
            description=(
                f"Scanning account {asked} returned {count} resource(s) owned by account "
                f"{owner}: check the role mapping for {asked}, or confirm they are shared"
            ),
            severity="warning",
        )
        for (asked, owner), count in sorted(mismatches.items())
    ]
    return fixed, gaps

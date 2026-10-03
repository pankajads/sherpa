from __future__ import annotations

import re
from typing import Any

from github import Github, GithubException

from sherpa.core.interfaces import ScannerPlane, ScannerPlugin, ScanResult, ValidationResult
from sherpa.core.models import CoverageGap, PackageDependency, Repository, ScanConfig
from sherpa.core.models.enums import ErrorClass, IaCType
from sherpa.scanners._github_errors import classify

# Account ID and region are optional in some ARNs (e.g. arn:aws:s3:::bucket-name)
_ARN_PATTERN = re.compile(r"arn:aws:[a-z0-9\-]+:[a-z0-9\-]*:[0-9]*:[^\s\"']+")


def _parse_requirements(content: str) -> list[PackageDependency]:
    deps = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_\-\.]+)\s*([><=!~^].*)?", line)
        if match:
            deps.append(
                PackageDependency(
                    name=match.group(1),
                    version_spec=(match.group(2) or "").strip(),
                    ecosystem="pypi",
                )
            )
    return deps


def _parse_package_json(content: str) -> list[PackageDependency]:
    import json

    data = json.loads(content)  # invalid JSON raises; the caller records a coverage gap
    if not isinstance(data, dict):
        raise ValueError("package.json is not a JSON object")
    deps = []
    for section in ("dependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            deps.append(PackageDependency(name=name, version_spec=version, ecosystem="npm"))
    return deps


def _parse_go_mod(content: str) -> list[PackageDependency]:
    deps = []
    in_require = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        if in_require or stripped.startswith("require "):
            parts = stripped.replace("require ", "").split()
            if len(parts) >= 2:
                deps.append(PackageDependency(name=parts[0], version_spec=parts[1], ecosystem="go"))
    return deps


_PACKAGE_PARSERS = {
    "requirements.txt": _parse_requirements,
    "package.json": _parse_package_json,
    "go.mod": _parse_go_mod,
}


def _detect_iac(file_names: set[str], content_map: dict[str, str]) -> IaCType:
    if any(f.endswith(".tf") for f in file_names):
        return IaCType.TERRAFORM
    if "cdk.json" in file_names:
        return IaCType.CDK
    for fname, content in content_map.items():
        if fname.endswith((".yaml", ".yml", ".json")) and "AWSTemplateFormatVersion" in content:
            return IaCType.CLOUDFORMATION
    return IaCType.NONE


def _extract_arns(content: str) -> list[str]:
    return list(dict.fromkeys(_ARN_PATTERN.findall(content)))  # dedup, preserve order


class GithubCodeScanner(ScannerPlugin):
    @property
    def scanner_type(self) -> str:
        return "github-code"

    @property
    def plane(self) -> ScannerPlane:
        return ScannerPlane.CODE

    def applies_to(self, config: ScanConfig) -> bool:
        return bool(config.github_org)

    async def validate_config(self, config: ScanConfig) -> ValidationResult:
        if not config.github_org:
            return ValidationResult.fail("github_org is required for github-code scanner")
        return ValidationResult.ok()

    async def scan(self, config: ScanConfig) -> ScanResult:
        gh = Github(config.github_token_value())
        repositories: list[Repository] = []
        coverage_gaps: list[CoverageGap] = []
        errors: list[str] = []

        try:
            org = gh.get_organization(config.github_org)
        except GithubException as exc:
            return ScanResult(
                scanner_type=self.scanner_type,
                errors=[f"Cannot access org {config.github_org}: {exc}"],
                coverage_gaps=[
                    CoverageGap(
                        description=f"GitHub org {config.github_org} could not be read: "
                        "no repositories were scanned",
                        severity="error",
                        scanner=self.scanner_type,
                        scope=f"github.com/{config.github_org}",
                        error_class=classify(exc),
                    )
                ],
            )

        for repo in sorted(org.get_repos(), key=lambda r: r.full_name):
            try:
                repo_obj = self._scan_repo(repo, config.github_org or "", coverage_gaps)
                repositories.append(repo_obj)
            except Exception as exc:  # noqa: BLE001 - one bad repo must not stop the scan
                errors.append(f"{repo.full_name}: {exc}")
                coverage_gaps.append(
                    CoverageGap(
                        description=f"Skipped repo {repo.full_name}: {exc}",
                        severity="warning",
                        scanner=self.scanner_type,
                        scope=f"github.com/{repo.full_name}",
                        error_class=classify(exc),
                    )
                )

        return ScanResult(
            scanner_type=self.scanner_type,
            repositories=repositories,
            coverage_gaps=coverage_gaps,
            errors=errors,
        )

    def _scan_repo(self, repo: Any, org: str, gaps: list[CoverageGap]) -> Repository:

        repo_id = f"github.com/{repo.full_name}"
        all_arns: list[str] = []
        all_deps: list[PackageDependency] = []
        file_names: set[str] = set()
        content_map: dict[str, str] = {}
        has_dockerfile = False
        has_docker_compose = False

        unreadable: list[tuple[str, Exception]] = []
        try:
            contents = repo.get_git_tree(repo.default_branch, recursive=True).tree
        except Exception as exc:  # noqa: BLE001 - reported as a coverage gap
            # Without a tree the repo would look empty (no IaC, no dependencies): say so.
            contents = []
            gaps.append(
                CoverageGap(
                    description=(
                        f"File list of {repo_id} could not be read: its IaC, dependencies "
                        "and resource references are missing"
                    ),
                    severity="warning",
                    scanner=self.scanner_type,
                    scope=repo_id,
                    error_class=classify(exc),
                )
            )

        for item in contents:
            path = item.path
            fname = path.rsplit("/", 1)[-1]
            file_names.add(fname)

            if fname == "Dockerfile":
                has_dockerfile = True
            if fname in ("docker-compose.yml", "docker-compose.yaml"):
                has_docker_compose = True

            # Only fetch content for relevant files (limit API calls).
            # YAML/JSON are fetched for CloudFormation detection in addition to package manifests.
            is_yaml_or_json = fname.endswith((".yaml", ".yml", ".json"))
            if (
                fname in _PACKAGE_PARSERS
                or path.endswith(".tf")
                or fname in ("cdk.json",)
                or is_yaml_or_json
            ):
                try:
                    file_content = repo.get_contents(path, ref=repo.default_branch)
                    if isinstance(file_content, list):
                        file_content = file_content[0]
                    decoded = file_content.decoded_content.decode("utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001 - reported as a coverage gap below
                    unreadable.append((path, exc))
                    continue
                content_map[fname] = decoded
                if fname in _PACKAGE_PARSERS:
                    try:
                        all_deps.extend(_PACKAGE_PARSERS[fname](decoded))
                    except ValueError as exc:
                        gaps.append(
                            CoverageGap(
                                description=(
                                    f"{path} in {repo_id} could not be parsed ({exc}): its "
                                    "dependencies are missing"
                                ),
                                severity="warning",
                                scanner=self.scanner_type,
                                scope=repo_id,
                                error_class=ErrorClass.INVALID_CONTENT,
                            )
                        )
                all_arns.extend(_extract_arns(decoded))

        if unreadable:
            first_path, first_exc = sorted(unreadable, key=lambda u: u[0])[0]
            gaps.append(
                CoverageGap(
                    description=(
                        f"{len(unreadable)} file(s) in {repo_id} could not be read (e.g. "
                        f"{first_path}): IaC, dependencies or resource references in them "
                        "are missing"
                    ),
                    severity="warning",
                    scanner=self.scanner_type,
                    scope=repo_id,
                    error_class=classify(first_exc),
                )
            )

        iac_type = _detect_iac(file_names, content_map)

        return Repository(
            id=repo_id,
            url=repo.clone_url,
            iac_type=iac_type,
            declared_resource_ids=sorted(set(all_arns)),
            package_dependencies=all_deps,
            has_dockerfile=has_dockerfile,
            has_docker_compose=has_docker_compose,
            default_branch=repo.default_branch or "main",
            metadata={"description": repo.description or "", "private": repo.private},
        )

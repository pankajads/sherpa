"""The AWS accounts file: which accounts to scan, in which regions, with which role.

Example (see examples/aws-accounts/):

    default_regions: [us-east-1]
    role_name: SherpaReadOnly
    accounts:
      - id: "111111111111"
        name: prod
        regions: [eu-west-1, eu-central-1]
      - id: "222222222222"
        role_arn: arn:aws:iam::222222222222:role/LegacyAudit
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, StrictStr, field_validator, model_validator

from .inventory import AwsAccountSettings


class AwsAccountEntry(BaseModel):
    # StrictStr: an unquoted YAML number would silently drop leading zeros of an account ID.
    id: StrictStr
    name: str = ""
    regions: list[str] = Field(default_factory=list)
    role_arn: str | None = None
    role_name: str | None = None
    external_id: str | None = None

    model_config = {"extra": "forbid"}


class AwsAccountsFile(BaseModel):
    default_regions: list[str] = Field(default_factory=list)
    role_name: str | None = None
    external_id: str | None = None
    accounts: list[AwsAccountEntry]

    model_config = {"extra": "forbid"}

    @field_validator("accounts", mode="before")
    @classmethod
    def _ids_must_be_quoted(cls, v: object) -> object:
        for entry in v if isinstance(v, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), int):
                raise ValueError(
                    f'account id {entry["id"]} must be quoted, e.g. id: "012345678901" '
                    "(unquoted YAML numbers lose leading zeros)"
                )
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> AwsAccountsFile:
        ids = [a.id for a in self.accounts]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate account ids: {', '.join(dupes)}")
        return self

    def account_settings(self) -> dict[str, AwsAccountSettings]:
        return {
            a.id: AwsAccountSettings(
                name=a.name,
                regions=a.regions,
                role_arn=a.role_arn,
                role_name=a.role_name,
                external_id=a.external_id,
            )
            for a in self.accounts
        }

    @classmethod
    def from_file(cls, path: str | Path) -> AwsAccountsFile:
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data or {})

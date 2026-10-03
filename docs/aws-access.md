# AWS access

Sherpa scans AWS **read-only**. This page covers how to tell it which accounts and regions to scan, and how it gets credentials for each one. It works the same for a single-account company and a multi-account AWS Organization.

## Single account

With one account and no role settings, Sherpa uses your **current credentials** (environment variables, a `~/.aws` profile, an SSO session, or an instance/container role) and assumes no role:

```bash
export AWS_PROFILE=acquired-co-readonly
sherpa discover --aws-account 123456789012 --regions us-east-1,eu-west-1
```

To assume a specific role in that one account instead, add `--assume-role arn:aws:iam::123456789012:role/<name>`.

## Several accounts

Sherpa assumes a role **in each account**, once per account, using credentials from the account you run it from:

```bash
sherpa discover \
  --aws-account 111111111111 --aws-account 222222222222 \
  --regions us-east-1 \
  --role-name SherpaReadOnly          # default when several accounts are given
```

Real estates rarely use the same regions everywhere, so for anything beyond a handful of accounts use an **accounts file** (examples in [`examples/aws-accounts/`](../examples/aws-accounts/)):

```yaml
default_regions: [us-east-1]       # for accounts that don't list their own
role_name: SherpaReadOnly          # arn:aws:iam::<account>:role/<role_name>
accounts:
  - id: "111111111111"             # always quote account IDs
    name: prod
    regions: [eu-west-1, eu-central-1]
  - id: "222222222222"
    role_arn: arn:aws:iam::222222222222:role/security/LegacyAudit
```

```bash
sherpa discover --accounts-file accounts.yaml
```

Before any API call, Sherpa prints the **scan plan**: every account, its regions, and the exact role (or "current credentials") it will use. Check it.

## How each account's credentials are chosen

First match wins:

1. The account's `role_arn` in the accounts file.
2. The account's `role_name` in the accounts file.
3. `--role-name` (or the file's top-level `role_name`).
4. `--assume-role`, which is valid only when scanning a single account.
5. Current credentials, when scanning a **single** account.
6. `SherpaReadOnly`, when scanning **several** accounts. Several accounts are never scanned with one set of credentials.

## Rules that protect you

- **Account IDs must be 12 digits and, in YAML, quoted.** Unquoted, `012345670123` is read as the octal number 1402433619, a different account. Sherpa rejects unquoted IDs.
- **A role must belong to the account it's used for.** `arn:aws:iam::222…:role/x` configured for account `111…` is rejected before any call.
- **`--assume-role` with several accounts is rejected.** It names one role in one account.
- **Each account is assumed once,** credentials are cached, and they are refreshed 5 minutes before expiry, so long scans don't fail halfway.
- **An account whose role can't be assumed is reported once,** as an `error` coverage gap, and the other accounts are still scanned.
- **Resources are labelled with the account in their ARN.** If scanning account A returns resources owned by account B (a wrong role mapping, or resources shared into A), they are labelled B and a coverage gap tells you to check the mapping.
- **IAM is scanned once per account** through its global endpoint, whether or not `us-east-1` is one of the account's regions.
- **Each region with no resources** gets a per-account warning, since that often means missing permissions.

## Setting up the role in each account

Until the published least-privilege policy lands ([P1-1.1](planning/02-phased-plan.md)), attach the AWS managed `ReadOnlyAccess` policy to the role. Its trust policy must allow the identity you run Sherpa as. For cross-company access, require an ExternalId:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::<scanner-account>:role/<scanner-role>" },
    "Action": "sts:AssumeRole",
    "Condition": { "StringEquals": { "sts:ExternalId": "<agreed-value>" } }
  }]
}
```

Pass the ExternalId with `--external-id`, the `SHERPA_AWS_EXTERNAL_ID` environment variable, or per account in the accounts file.

## Finding Sherpa's calls in CloudTrail

Every assumed-role session is named **`SherpaDiscovery`**. The target's security team can filter CloudTrail on that session name to review every call Sherpa made, all of which should be read-only.

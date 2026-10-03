# Sandbox AWS Organization

The real AWS accounts the reference estate (#15) is built in, scanned and destroyed. They are separate from anything else, so purging them can't hit real resources.

| Account | Role in the test |
|---|---|
| `sherpa-ci` | The acquirer's side. GitHub Actions signs in here through OIDC, holds the Terraform state and runs Sherpa. |
| `sherpa-ref-prod`, `sherpa-ref-dev`, `sherpa-ref-shared` | The "acquired company". Terraform builds the estate here; Sherpa scans it. |

All four are in the `sherpa-sandbox` OU. Nothing runs in the management account.

## What's here

| File | What it is |
|---|---|
| [`scp-sandbox-guardrails.json`](scp-sandbox-guardrails.json) | Service control policy for the OU. Limits the accounts to `us-east-1` + `eu-west-1`, an allow-list of services, `t3`/`t4g` micro and nano instances, and `db.t3`/`db.t4g` micro databases. It also blocks NAT gateways, reserved capacity, IAM users and access keys, leaving the Organization, disabling CloudTrail, and changes to the bootstrap roles. |
| [`bootstrap.yaml`](bootstrap.yaml) | CloudFormation template, deployed once as a StackSet to the OU. See below. |
| [`smoke.sh`](smoke.sh) | Proves the bootstrap works and the guardrails hold. Run by [`sandbox-smoke.yml`](../../.github/workflows/sandbox-smoke.yml). |

### What the bootstrap creates

**In `sherpa-ci`:**
- the GitHub OIDC provider;
- the **`SherpaRefCi`** role. Only jobs in this repo's `sandbox` GitHub environment can assume it. It can do two things: assume the two roles below in the target accounts, and use the state bucket;
- the Terraform state bucket `sherpa-ref-tfstate-<account>`: versioned, encrypted, TLS-only, never public.

**In each target account:**
- **`SherpaReadOnly`**: what Sherpa scans with. It grants exactly the 15 read calls the AWS collector makes, nothing else, and requires an ExternalId. If the collector gains a call, add it here; the smoke test fails on any `access_denied` gap until you do.
- **`SherpaRefEstateDeploy`**: what Terraform uses. It can use the estate's services, but IAM writes are limited to `/sherpa-ref/workload/`, and every role it creates must carry the `SherpaRefWorkloadBoundary` permissions boundary, so it can't create a role more powerful than the estate needs. It is scoped by service, not by resource; the SCP and the fact that these accounts hold nothing else make up the rest.

There are **no IAM users or access keys anywhere**: GitHub gets short-lived credentials through OIDC for each run.

## Setting it up (once)

All steps happen in the management account's console, in `us-east-1`.

1. **Attach the SCP.**
   - Go to Organizations → Policies → Service control policies → Create policy.
   - Paste [`scp-sandbox-guardrails.json`](scp-sandbox-guardrails.json) and name it `sherpa-sandbox-guardrails`.
   - Go to Targets → Attach, and choose the `sherpa-sandbox` OU.
2. **Deploy the bootstrap StackSet.**
   - Go to CloudFormation → StackSets → Create StackSet and choose **Service-managed permissions**.
   - Upload [`bootstrap.yaml`](bootstrap.yaml) and name it `sherpa-sandbox-bootstrap`.
   - Parameters:
     - `CiAccountId`: the `sherpa-ci` account ID;
     - `TargetAccountIds`: the three `sherpa-ref-*` account IDs, comma-separated;
     - leave the rest at their defaults. `GitHubRepository` is in the form GitHub uses in its OIDC tokens, `owner@ownerId/repo@repoId` (for this repo, `pankajads@21303111/sherpa@1303466252`). The plain `owner/repo` form fails with "Not authorized to perform sts:AssumeRoleWithWebIdentity". For a fork, read your own value from the smoke test's **Show OIDC token claims** step.
   - Deployment targets: the `sherpa-sandbox` OU. Turn **automatic deployment on**. Region: **US East (N. Virginia)** only.
   - Acknowledge that it creates named IAM resources, then submit. All four stack instances should reach `SUCCEEDED` in a few minutes.
3. **Create the GitHub environment.**
   - In the repo, go to Settings → Environments → New environment and name it `sandbox`.
   - Deployment branches and tags: **Selected branches** → `main`.
   - Add these **environment variables** (not secrets; none of them are credentials):

     | Name | Value |
     |---|---|
     | `SANDBOX_CI_ACCOUNT_ID` | the `sherpa-ci` account ID |
     | `SANDBOX_TARGET_ACCOUNT_IDS` | the three target account IDs, space-separated |
     | `SANDBOX_EXTERNAL_ID` | `sherpa-ref-sandbox` (the template's default) |
4. **Run the smoke test.** Go to Actions → Sandbox smoke test → Run workflow. It must pass before the estate is built on top.

## The smoke test

It creates nothing: every write is either a dry run or expected to be denied. It checks:
- GitHub signs in as `SherpaRefCi` in the CI account.
- In every target account:
  - both roles can be assumed, and each lands in the account it should;
  - `SherpaReadOnly` refuses to be assumed without the ExternalId, and can't write;
  - the deploy role can launch a `t3.micro` (dry run);
  - the deploy role **can't** launch an `m5.large`, work in another region, create an IAM user, create a role outside `/sherpa-ref/workload/`, or edit `SherpaReadOnly`.
- A real multi-account `sherpa discover` of the target accounts exits 0, verifies every account's identity and reports no `access_denied` gaps.

The repository is public, so its log masks account IDs and prints only counts. No snapshot is uploaded.

## Changing or removing it

- **Changing the bootstrap:** edit `bootstrap.yaml` and update the StackSet. The SCP stops anyone else, including the deploy role, from changing the bootstrap roles.
- **Removing everything:**
  1. Run `terraform destroy` for the estate.
  2. Delete the StackSet's instances, then the StackSet.
  3. The state bucket is retained on purpose; empty it and delete it by hand.
  4. Close the member accounts if you no longer need them. AWS limits how many accounts can be closed in 30 days, and closed accounts stay suspended for 90 days, which is why the accounts are permanent and only the estate inside them is created and destroyed.

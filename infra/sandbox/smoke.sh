#!/usr/bin/env bash
# Sandbox smoke test: proves the bootstrap works and its guardrails hold, then runs a real
# multi-account Sherpa scan of the (empty) target accounts. Creates nothing; every write is a
# dry run or is expected to be denied.
#
# Expects to run with the SherpaRefCi role's credentials (GitHub OIDC) and:
#   CI_ACCOUNT   sherpa-ci account ID
#   TARGETS      target account IDs, space- or comma-separated
#   SHERPA_AWS_EXTERNAL_ID   ExternalId of SherpaReadOnly
set -euo pipefail

: "${CI_ACCOUNT:?}" "${TARGETS:?}" "${SHERPA_AWS_EXTERNAL_ID:?}"
TARGETS=${TARGETS//,/ }
export AWS_REGION=${AWS_REGION:-us-east-1} AWS_PAGER=""
REGIONS="us-east-1,eu-west-1"   # the two regions the SCP allows
failures=0

pass() { echo "  ok   $*"; }
fail() { echo "  FAIL $*"; failures=$((failures + 1)); }

# assume ROLE_ARN [EXTERNAL_ID] -> prints the Credentials JSON
assume() {
  aws sts assume-role --role-arn "$1" --role-session-name SherpaSmoke \
    ${2:+--external-id "$2"} --query Credentials --output json
}

# as CREDS_JSON CMD... -> runs CMD with those credentials
as() {
  local creds=$1; shift
  AWS_ACCESS_KEY_ID=$(jq -r .AccessKeyId <<<"$creds") \
    AWS_SECRET_ACCESS_KEY=$(jq -r .SecretAccessKey <<<"$creds") \
    AWS_SESSION_TOKEN=$(jq -r .SessionToken <<<"$creds") \
    "$@"
}

# expect_denied WHAT CMD... -> CMD must fail with an authorization error
expect_denied() {
  local what=$1 out; shift
  if out=$("$@" 2>&1) || grep -q DryRunOperation <<<"$out"; then
    fail "$what: expected to be denied, but it was allowed"
  elif grep -qE 'AccessDenied|UnauthorizedOperation|not authorized' <<<"$out"; then
    pass "$what: denied"
  else
    fail "$what: failed for another reason: $out"
  fi
}

# expect_dry_run_ok WHAT CMD... -> CMD is an EC2 dry run that would have been allowed
expect_dry_run_ok() {
  local what=$1 out; shift
  out=$("$@" 2>&1) || true
  if grep -q DryRunOperation <<<"$out"; then
    pass "$what: allowed (dry run, nothing created)"
  else
    fail "$what: expected DryRunOperation, got: $out"
  fi
}

check_account() {  # WHAT CREDS EXPECTED_ACCOUNT
  local got
  got=$(as "$2" aws sts get-caller-identity --query Account --output text)
  if [[ $got == "$3" ]]; then pass "$1 is in the expected account"; else fail "$1 is in account $got"; fi
}

echo "== CI role"
got=$(aws sts get-caller-identity --query Arn --output text)
if [[ $got == "arn:aws:sts::$CI_ACCOUNT:assumed-role/SherpaRefCi/"* ]]; then
  pass "signed in as SherpaRefCi in the CI account"
else
  fail "unexpected identity: $got"
fi
expect_denied "CI role creating an IAM user" aws iam create-user --user-name sherpa-smoke

for acct in $TARGETS; do
  echo "== Target account ending ${acct: -4}"
  ro_arn="arn:aws:iam::$acct:role/SherpaReadOnly"
  deploy_arn="arn:aws:iam::$acct:role/sherpa-ref/SherpaRefEstateDeploy"

  expect_denied "SherpaReadOnly without the ExternalId" assume "$ro_arn"
  ro=$(assume "$ro_arn" "$SHERPA_AWS_EXTERNAL_ID")
  check_account "SherpaReadOnly" "$ro" "$acct"
  expect_denied "SherpaReadOnly creating a queue" \
    as "$ro" aws sqs create-queue --queue-name sherpa-smoke

  deploy=$(assume "$deploy_arn")
  check_account "SherpaRefEstateDeploy" "$deploy" "$acct"
  ami="resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
  expect_dry_run_ok "Deploy role: t3.micro instance" \
    as "$deploy" aws ec2 run-instances --dry-run --instance-type t3.micro --image-id "$ami"
  expect_denied "Deploy role: m5.large instance (SCP)" \
    as "$deploy" aws ec2 run-instances --dry-run --instance-type m5.large --image-id "$ami"
  expect_denied "Deploy role: region outside the allowed two (SCP)" \
    as "$deploy" aws sqs list-queues --region ap-south-1
  expect_denied "Deploy role: creating an IAM user (SCP)" \
    as "$deploy" aws iam create-user --user-name sherpa-smoke
  expect_denied "Deploy role: role outside /sherpa-ref/workload/" \
    as "$deploy" aws iam create-role --role-name sherpa-smoke \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[]}'
  expect_denied "Deploy role: editing SherpaReadOnly (SCP)" \
    as "$deploy" aws iam put-role-policy --role-name SherpaReadOnly --policy-name x \
    --policy-document '{"Version":"2012-10-17","Statement":[]}'
done

echo "== Sherpa multi-account scan (read-only)"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
accounts=()
for acct in $TARGETS; do accounts+=(--aws-account "$acct"); done
set +e
sherpa discover "${accounts[@]}" --regions "$REGIONS" --role-name SherpaReadOnly \
  --db "$work/sherpa.db" --output "$work/out" >"$work/discover.log" 2>&1
rc=$?
set -e
if [[ $rc -eq 0 ]]; then pass "sherpa discover exited 0"; else fail "sherpa discover exited $rc"; fi

# Summarise without printing resource names or the snapshot (this log is public).
snapshot=$(find "$work/out" -name "snapshot_*.json" 2>/dev/null | head -1 || true)
if [[ -z $snapshot ]]; then
  fail "no snapshot written"
  tail -n 20 "$work/discover.log"
else
  python3 - "$snapshot" "$TARGETS" <<'PY' || failures=$((failures + 1))
import collections, json, sys

snap = json.load(open(sys.argv[1]))
targets = sys.argv[2].split()
ok = True
verified = {i["scope"] for i in snap.get("scan_identities", []) if i.get("verified")}
for acct in targets:
    if f"aws-account:{acct}" not in verified:
        print(f"  FAIL account ending {acct[-4:]} not verified")
        ok = False
denied = [g for g in snap.get("coverage_gaps", []) if g.get("error_class") == "access_denied"]
for g in denied:
    # A missing permission in SherpaReadOnly: name the service, not the resource.
    print(f"  FAIL access denied: {','.join(g.get('affected_services', []))} — {g.get('description', '')[:120]}")
    ok = False
types = collections.Counter(r["resource_type"] for r in snap.get("resources", []))
print(f"  info {sum(types.values())} resources: " + ", ".join(f"{t} {n}" for t, n in sorted(types.items())))
print(f"  info {len(snap.get('coverage_gaps', []))} coverage gaps, {len(denied)} access denied")
if ok:
    print("  ok   all target accounts verified; no access-denied gaps")
sys.exit(0 if ok else 1)
PY
fi

echo
if [[ $failures -eq 0 ]]; then echo "Smoke test passed."; else echo "Smoke test FAILED: $failures check(s)."; exit 1; fi

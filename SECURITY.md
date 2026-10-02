# Security Policy

Sherpa is designed to be run against cloud accounts and source code of companies being acquired. We treat security issues as the highest priority.

## Reporting a vulnerability

**Please don't open a public issue.** Report privately through GitHub's [private vulnerability reporting](https://github.com/pankajads/sherpa/security/advisories/new) (Security → Report a vulnerability).

Include what you found, how to reproduce it, and the impact you see. We aim to acknowledge reports within **3 working days**, and to agree a fix and disclosure timeline with you. We'll credit you in the advisory unless you prefer otherwise.

## What Sherpa promises

A violation of any of these is a security vulnerability:

1. **Read-only.** Collectors call only read APIs against AWS and GitHub. Any code path that can modify a scanned environment is a vulnerability.
2. **No credential persistence.** Tokens, keys and temporary credentials are never written to snapshots, reports, the database, bundles or logs. CI enforces this with a sentinel-credential test and a gitleaks scan of all outputs.
3. **No secret or data-content collection.** Sherpa does not read secret values, environment variable values, object contents or database rows.
4. **No telemetry.** Sherpa makes no network calls other than to the AWS, GitHub (and, in later phases, explicitly configured) endpoints you point it at.

## In scope
- Code in this repository: scanners, core, CLI, and, once they exist, offline bundles and the web GUI.
- Supply chain: dependencies and CI workflows in this repository.

## Out of scope
- Vulnerabilities in AWS, GitHub or third-party dependencies themselves. Report those upstream; tell us too if Sherpa is affected.
- Findings that require an attacker to already control the machine running Sherpa.

## Supported versions
Sherpa is pre-1.0. Only the latest `main` is supported; fixes are not backported.

## Known issue (fixed)
Versions before the fix for [#7](https://github.com/pankajads/sherpa/issues/7) wrote the GitHub token in plain text into `snapshot_*.json` and SQLite files created with `--db`. If you used those, rotate the token and delete or scrub the files.

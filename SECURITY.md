# Security Policy

## Supported release scope

This repository is an offline/testable data-agent MVP. It contains no production credentials, customer data, execution receipts, session history, or runtime audit trails.

## Do not commit

Do not commit any of the following:

- `.env` files, API keys, tokens, passwords, private keys, certificates, or connection strings;
- runtime sessions, audit trails, model/tool traces, execution receipts, or human-review queues;
- downloaded datasets, raw customer data, generated reports, benchmark artifacts, caches, or local databases;
- internal-only planning notes and unpublished assessment material.

The root `.gitignore` excludes these categories by default. Before committing, run:

```bash
git status --short
git diff --cached --check
git ls-files | findstr /I ".env sessions artifacts harness/reports"
```

The last command should not list local-only paths (except the tracked `.env.example`).

## Reporting a vulnerability

Please do **not** open a public issue containing secrets, PII, exploit payloads, or customer data. Contact the repository owner privately with:

- an affected component and version/commit;
- safe reproduction steps;
- impact assessment; and
- a recommended remediation, if available.

## Deployment boundary

The included adapters, fixtures, and measurements are designed for local/offline validation. Production deployment requires independent security review, identity and tenant isolation, managed secrets, external data-governance review, and infrastructure controls appropriate to the target environment.

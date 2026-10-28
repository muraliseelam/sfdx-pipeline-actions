# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through
[GitHub Security Advisories](https://github.com/muraliseelam/sfdx-pipeline-actions/security/advisories/new).
That is the only reporting channel, and it is monitored — the maintainer's
public address is a `noreply` one and cannot receive mail.

Please include:

- what the issue is and where in the pipeline it occurs,
- how to reproduce it,
- what an attacker could achieve.

We aim to acknowledge a report within three working days.

## Supported versions

| Version | Supported |
|---|---|
| `v1.x` (the `v1` moving tag) | ✅ |
| `main` | ⚠️ development branch, no guarantees |
| anything older | ❌ |

## Security model you should understand before using this

This project is a set of **reusable GitHub Actions workflows that deploy to
Salesforce orgs**. That makes it part of your supply chain. A few properties
are worth being explicit about.

### Pin a tag, never a branch

Consumers must reference a released tag:

```yaml
uses: muraliseelam/sfdx-pipeline-actions/.github/workflows/deploy.yml@v1
```

Referencing `@main` means you execute whatever is on our development branch at
the moment your workflow runs. For maximum strictness, pin a full commit SHA.

Every release tag in this repository also **rewrites its own internal action
references to itself**, so pinning `@v1` pins the whole dependency tree, not
just the entry-point file. You can verify any tag:

```bash
git show v1:.github/workflows/deploy.yml \
  | grep -oE 'sfdx-pipeline-actions/[^@]+@[A-Za-z0-9.]+' | sed 's|.*@||' | sort -u
# expect: v1   (never: main)
```

### Secrets never leave your repository

The workflows read credentials only through `${{ secrets.* }}` supplied by the
calling repository's GitHub Environments. This project contains no credentials,
no org identifiers and no endpoints of its own. Nothing is transmitted anywhere
except to Salesforce, your Git host, and the optional integrations you
explicitly configure (chat webhook, issue tracker).

### Authentication

Org authentication uses the **JWT bearer flow** with a Connected App. The
private key lives in a GitHub Environment secret, scoped per environment, and
is written to a temporary file that is removed when the runner is torn down.
Prefer one Connected App and one integration user per org.

### Protect the production environment

The production stage is deliberately split into two GitHub Environments —
`prod-validate` (unattended check-only) and `prod` (required reviewers,
quick-deploy). The human gate is only as strong as the reviewer list you
configure on the `prod` environment. Configure it.

Project Init limits `prod` and `prod-validate` to the production branch.
Sandbox environments (`int`, `qa`, `stage`) are usable by any workflow in the
repository, including a pull request's own copy of the caller workflows, so their
org credentials are trusted to everyone with write access. Restricting them to
protected branches would also block the pipeline's own PR validation (it runs on
`refs/pull/<n>/merge`), so this is a deliberate trade-off: give the sandbox
integration users only the permissions a deploy needs.

### Notifications are not a gate

Chat notifications are best-effort telemetry. A failing or misconfigured chat
integration emits a warning annotation and never fails a deployment. Do not
build an approval control on top of them.

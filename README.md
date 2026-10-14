# Salesforce DevOps Pipeline for GitHub Actions

[![Pipeline Self-Test](https://github.com/muraliseelam/sfdx-pipeline-actions/actions/workflows/sf-pipeline-selftest.yml/badge.svg)](https://github.com/muraliseelam/sfdx-pipeline-actions/actions/workflows/sf-pipeline-selftest.yml)

A complete, config-driven CI/CD pipeline for Salesforce: you list your environments, run one setup
command, and every pull request is validated against the right org, approved with a label, and
promoted up to production. It works with 2–6 stages and any environment names.

```
feature/* ──PR──▶ dev ──promote:int──▶ int ──promote:qa──▶ qa ──promote:stage──▶ stage ──release──▶ main (production)
```

**What you get**

- **Delta deploys only.** sfdx-git-delta builds `package.xml` + `destructiveChanges.xml` from
  `merge-base(main, HEAD)`, so every environment receives the same prod-relative artifact.
- **Smart Apex tests.** `RunSpecifiedTests` with each changed class's companion test. A class
  without a test fails fast; there's no silent fallback to running every test.
- **🚀 PR report.** Component and test failures (also inline on the diff), coverage per changed
  class against your threshold, and the delta manifest.
- **🔍 Code analysis.** Salesforce Code Analyzer on the changed files, blocking on High/Critical.
- **Label-driven flow.** `approved` approves, auto-merges and deploys; `promote:<env>` opens the next
  promotion PR; `deploy:<env>` test-deploys an open PR.
- **Safe production releases.**
  1. *Prepare* validates check-only against production.
  2. The `approved` label quick-deploys the validated job.
  3. The pipeline merges with a merge commit, tags, publishes a GitHub Release, and re-syncs the
     env branches.

  Resumable, and it never deploys twice.
- **Deploy safety nets.** A dependency lint for deleted metadata that is still referenced,
  incremental profile and permission-set deploys, and an admin-only bypass label for the quality
  gates.
- **Operations.** One-click rollback (a revert PR through the pipeline), a durable audit-log
  branch, and Slack/Teams/Webex/webhook notifications plus Jira transitions.
- **Guardrails.** Branch protection with required checks, PR routing, meta-pair and
  "cut from main" checks, approval guards, per-org deploy locks, and production credentials scoped
  to protected branches.
- **Two auth methods.** SFDX auth URL (no app needed) or JWT through an auto-created External
  Client App.
- **Setup tooling.** `bootstrap.py` configures GitHub, copies your org logins into secrets, and
  diagnoses problems; a Doctor workflow checks every org login weekly.

## Quick start

```bash
pip install -r sf-pipeline/scripts/requirements.txt
python sf-pipeline/scripts/bootstrap.py init --envs dev,int,qa,stage,prod   # your names, in order
git commit -am "Configure pipeline" && git push

sf org login web --alias dev --instance-url https://test.salesforce.com    # …once per env
python sf-pipeline/scripts/bootstrap.py setup --pat-from-gh                 # GitHub + secrets + doctor
```

No sandboxes? `bootstrap.py scratch-orgs --dev-hub <hub>` creates one scratch org per env.
Then open a PR into your first env and add the `approved` label.
**[Getting started →](docs/getting-started.md)**

## What it needs

| | |
|---|---|
| Per environment | One secret `SF_<ENV>_AUTH_URL`, or three for JWT. `bootstrap.py` creates them from your CLI logins |
| Once | `SF_PIPELINE_PAT`: a token so the pipeline's own PRs and merges trigger workflows |
| You | Admin on the GitHub repo; Salesforce CLI, GitHub CLI and Python 3.10+ locally |

## Docs

| | |
|---|---|
| [Getting started](docs/getting-started.md) | From zero to a green PR |
| [Configuration](docs/configuration.md) | Every setting and its default |
| [Authentication](docs/authentication.md) | Auth URL vs JWT, the pipeline token, scratch orgs |
| [How it works](docs/how-it-works.md) | Workflows, checks, approvals, releases, trust model |
| [Troubleshooting](docs/troubleshooting.md) | Every message and its fix |
| [Test report](docs/TEST_CHECKLIST.md) | The end-to-end scenarios this pipeline was verified with |

## License

MIT. See [LICENSE](LICENSE).

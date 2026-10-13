<div align="center">

# ⚡ sfdx-pipeline-actions

**A complete Salesforce delivery pipeline, as reusable GitHub Actions workflows**

*One copy of the pipeline. Every Salesforce repo gets the same CI/CD — with zero logic duplicated.*

**[📘 Product Documentation](./docs/Product_Documentation.md)** &nbsp;·&nbsp; **[🚀 User Guide](./docs/Pipeline_User_Guide.md)** &nbsp;·&nbsp; **[🤝 Contributing](./CONTRIBUTING.md)**

</div>

---

## 🧭 What this is

A production-grade Salesforce DevOps pipeline built entirely from **reusable
GitHub Actions workflows**: pull-request validation, a gated entry deploy,
label-driven promotion through your environments, a reviewer-gated production
release train, and day-2 operational runbooks.

The pipeline logic lives here, once. Your Salesforce project repositories call
it. They hold **no pipeline logic of their own**.

> [!IMPORTANT]
> There is **no Salesforce-side application** to install. GitHub Actions *is* the
> entire product. A consuming repository holds only thin caller workflows, one
> `pipeline.config.yml`, and its `force-app/`. **Nothing else is duplicated.**

---

## ✨ Why you might want it

- **One config file per project.** Branching, orgs, gates, test levels, quality
  thresholds and integrations are all driven by `pipeline.config.yml`. Onboarding
  a new project is copying 8 caller files and editing one YAML.
- **A change can never skip an environment.** The ordering guard trusts only a
  real deploy, recorded as a commit status — not a label someone could add.
- **Delta deployments.** Only what actually changed is deployed, diffed either
  against the target branch or against your production baseline.
- **Quality gates that mean something.** Apex coverage thresholds, dependency
  linting, profile handling, and an admin-only bypass whose permission is
  re-verified at run time, not trusted from the label.
- **A production release train** with validation and deployment split across two
  GitHub Environments, so the long check-only validation runs unattended and the
  human approval sits immediately before a fast quick-deploy.
- **A durable audit trail** of every deployment, independent of Actions log
  retention.

---

## 🔄 How a change flows

```text
  🌿 feature/PROJ-123
        │
        ├─ PR → int ──── 🔍 pr-validation ──── ✅ ──── 👤 approve
        │                (gates + check-only)              │
        │                                                  ▼
        │                                     🚀 real deploy → INT → auto-merge
        │
        ├─ 🏷️ deploy:qa ──── 🛡️ ordering guard ──── 🚀 deploy → QA → merge
        │
        ├─ 🏷️ deploy:stage ── 🛡️ ordering guard ──── 🚀 deploy → STAGE → merge
        │
        └─ 🎯 Prod Release:  assemble → validate → reviewer gate → quick-deploy → tag
```

> [!NOTE]
> The pipeline keeps **no database** — labels and commit statuses *are* the state.
> Only a **real deploy** ever writes a `deploy-<env>` commit status, which is
> exactly what the ordering guard trusts. A change can never skip an environment.

| 🏷️ Label | Who sets it | Meaning |
|:--|:--|:--|
| `approved` | reviewer | 🔓 triggers the **real** entry deploy, then auto-merge |
| `deploy:<env>` | human | ➡️ **instruction** — promote this change to `<env>` |
| `deployed:<env>` | pipeline | ✅ **record** — this commit really landed in `<env>` |
| `bypass-quality-gates` | 🔐 admin only | ⏭️ skip quality gates *(permission re-verified at run time)* |

---

## 🚀 Quick start

**1.** Copy the example consumer into your Salesforce repository:

```bash
cp -r examples/consumer/.github/workflows/. .github/workflows/
cp sf-pipeline/pipeline.config.example.yml sf-pipeline/pipeline.config.yml
```

**2.** Point the callers at your copy of this repository:

```bash
grep -rl 'your-org/sfdx-pipeline-actions' .github/workflows/ \
  | xargs sed -i '' 's|your-org/sfdx-pipeline-actions|YOUR_ORG/sfdx-pipeline-actions|g'
```

**3.** Edit `sf-pipeline/pipeline.config.yml` — your orgs, branches and gates.
**This is the only file you edit per project.** Name your environments whatever
you like: `dev/int/qa/stage` and `mon/tue/wed/thu` work equally well, and the
chain can be any length.

**4.** Set the `branches:` filter in `.github/workflows/pr-validation.yml` to your
**entry** branch. It is the one environment name that lives outside the config.

**5.** Run the **Project Init** workflow from the Actions tab (dry run first). It
creates every branch, label and GitHub Environment your config declares.

➡️ **[Full step-by-step — User Guide, Part 1](./docs/Pipeline_User_Guide.md#part-1--set-up-the-pipeline-one-time)**

---

## 🔌 What a caller looks like

A caller is a trigger plus one `uses:` — **never any logic**:

```yaml
name: PR Validation

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]
    branches: [dev]          # 👈 the entry branch — the ONLY literal outside the config

permissions:
  contents: write
  pull-requests: write
  deployments: write
  statuses: write

jobs:
  validate:
    uses: your-org/sfdx-pipeline-actions/.github/workflows/pr-validation.yml@v1
    secrets: inherit
```

> [!WARNING]
> **Never set `concurrency` on a caller.** A caller sharing a concurrency group
> with the reusable workflow it invokes **cancels itself** and produces a run with
> **zero jobs**. Concurrency is owned by the reusable workflow.

📌 A complete, working set of all 8 callers lives in
[`examples/consumer/`](./examples/consumer/).

---

## 🗂️ What's in here

| Path | Purpose |
|:--|:--|
| 📁 `.github/workflows/` | **10 reusable workflows** — `pr-validation` · `deploy` · `approve-guard` · `sf-promote-label` · `prod-release` · `sync-env-branches` · `org-bootstrap` · 3 × `sf-ops-*` |
| 📁 `sf-pipeline/<name>/` | **14 composite actions** — `plan` · `changes` · `deploy` · `guard` · `approval` · `bypass` · `merge-pr` · `report` · `status` · `release-gather` · `release-preflight` · `sync-branches` · `jira` · `notify` |
| 📁 `sf-pipeline/scripts/` | Shared shell + Python helpers |
| 📄 `sf-pipeline/pipeline.config.example.yml` | Annotated template for a consumer's config |
| 📁 `sf-setup/` | Org bootstrap kit — connected app, permission set, scripts |
| 📁 `examples/consumer/` | A working consumer: 8 thin callers + one config |
| 📁 `scripts/` | Maintainer tooling — validate, set owner, cut and verify releases |
| 📁 `docs/` | The two documents 📘🚀 and the editable diagrams |

> [!WARNING]
> **Every pipeline workflow here is `on: workflow_call` only.** None triggers on
> `push` or `pull_request`, so this repository can never self-trigger a deploy.
> (`ci.yml` is the one exception — it only runs static checks.)

---

## 🧬 Portability — the load-bearing rule

| | Runs in | May call `sf-pipeline/scripts/*`? |
|:--|:--|:--|
| 🔴 **Reusable workflow** | the **caller's** checkout — this repo is *not* there | ❌ **Never.** Always go through a composite action |
| 🟢 **Composite action** | `$GITHUB_ACTION_PATH` — fetched by slug | ✅ Yes, via `$GITHUB_ACTION_PATH/../scripts/…` |

The **only** file read from the caller's workspace is
`sf-pipeline/pipeline.config.yml` — precisely why consumers keep it.

> [!CAUTION]
> Breaking this rule is the single most common way to break every consumer at
> once. `./scripts/validate.sh` checks for it.
> 📖 [Product Documentation §3](./docs/Product_Documentation.md#3-portability--how-cross-repo-path-resolution-works)

---

## 🔖 Versioning

Pin a release tag — never a branch:

```yaml
uses: your-org/sfdx-pipeline-actions/.github/workflows/pr-validation.yml@v1
```

| Ref | Behaviour | Use it when |
|:--|:--|:--|
| `@v1` | **Recommended.** Moving major tag — advances on every backward-compatible release | You want fixes automatically, breaking changes never |
| `@v1.1.0` | Frozen at an exact build | You need a reproducible, pinned pipeline |
| `@main` | Bleeding edge — every commit lands instantly | Never, for a real project |

Breaking changes ship as `v2`; you opt in by changing one character.

> [!IMPORTANT]
> **Maintainers: never tag `main` directly.** These workflows reference this
> repository's own composite actions **45 times**, and on `main` every one of
> those reads `@main`. A tag cut straight from `main` would leave consumers
> pinned in name only while the real logic kept floating. Each release tag
> therefore carries a commit that rewrites those references to itself:
>
> ```bash
> ./scripts/cut-release.sh v1.2.0 --push
> ./scripts/verify-tag.sh  v1.2.0
> ```
>
> See [CONTRIBUTING.md](./CONTRIBUTING.md#releasing-maintainers).

---

## 📚 Documentation

Two documents, and **only** two:

| | Document | Read it for |
|:--:|:--|:--|
| 📘 | **[Product_Documentation.md](./docs/Product_Documentation.md)** | **How it works** — architecture & diagrams · branching strategy · environments & secrets · full config reference · all four stages · label/status state machine · delta baselines · profiles · quality gates · audit trail · ops runbooks · workflow/action/script catalogue |
| 🚀 | **[Pipeline_User_Guide.md](./docs/Pipeline_User_Guide.md)** | **How to use it** — ship a change day to day · promote and release · onboard a new project in 10 steps · the four ops runbooks · troubleshooting · quick reference |

🖼️ Editable draw.io sources and rendered PNGs → [`docs/diagrams/`](./docs/diagrams/)

---

## 🤝 Contributing

Issues and pull requests are welcome — please read
**[CONTRIBUTING.md](./CONTRIBUTING.md)** first, especially the portability rule
above. Security reports go through **[SECURITY.md](./SECURITY.md)**, not the
public issue tracker.

Run the static checks before opening a pull request:

```bash
./scripts/validate.sh
```

---

## 📄 Licence

[MIT](./LICENSE).

---

<div align="center">
<sub>🔒 Deploy-ordering guards &nbsp;·&nbsp; 🧾 Durable audit log &nbsp;·&nbsp; ⚙️ One config file per project</sub>
</div>

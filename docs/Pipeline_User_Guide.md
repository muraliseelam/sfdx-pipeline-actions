# Salesforce DevOps Pipeline — User Guide

**Step-by-step instructions for using the pipeline.** Follow the part that matches
your role.

> **Want to know how it works under the hood?** See
> **[Product_Documentation.md](./Product_Documentation.md)** — architecture,
> diagrams, branching strategy and every technical detail.

---

## Table of contents

| Part | For | Section |
|---|---|---|
| 1 | Developers | [Ship a change — day to day](#part-1--ship-a-change-day-to-day) |
| 2 | Release managers | [Promote and release](#part-2--promote-and-release) |
| 3 | Platform / DevOps | [Onboard a new project](#part-3--onboard-a-new-project) |
| 4 | Anyone on call | [Ops runbooks](#part-4--ops-runbooks) |
| 5 | Everyone | [Troubleshooting](#part-5--troubleshooting) |
| 6 | Everyone | [Quick reference](#part-6--quick-reference) |

Examples use the reference project **`sfdx-pipeline-example`**, whose chain is
`int → qa → stage → main(prod)`. Substitute your own environment names.

---

## Part 1 — Ship a change, day to day

### Step 1. Cut a feature branch from the baseline

Always branch from the **baseline branch** (`main` — what production has), never
from `int`. Name the branch with the Jira key so the pipeline can link the story:

```bash
git fetch origin
git checkout -b feature/PROJ-123-add-release-note-fields origin/main
```

> If the branch already exists and is stale, use the **Ops — Recreate Feature
> Branch** runbook instead of reusing it (see [Part 4](#part-4--ops-runbooks)).

### Step 2. Make your change and push

Edit metadata under `force-app/`, then:

```bash
git add force-app
git commit -m "feat(PROJ-123): add pipeline verification fields to Release_Note__c"
git push -u origin feature/PROJ-123-add-release-note-fields
```

### Step 3. Open a PR into the entry branch (`int`)

```bash
gh pr create --base int --title "PROJ-123: add release note fields" --body "..."
```

**PR Validation** starts automatically. It:

- computes the delta with sfdx-git-delta and lists the changed components,
- runs the quality gates that apply to your change (PMD for Apex, Sonar for Apex
  or LWC/Aura, coverage for Apex),
- runs a **check-only** validation deploy against the INT org,
- posts **one sticky comment** with the result,
- writes the `pr-validation` commit status.

Nothing is deployed for real at this point.

### Step 4. Read the PR comment

The sticky comment is updated in place on every push — there is only ever one.
It tells you which components changed, whether validation passed, and coverage if
Apex was involved.

If it fails, push a fix. Validation re-runs and supersedes the previous run.

### Step 5. Get an approval

Once validation is green, a reviewer approves the PR. The pipeline then adds the
`approved` label, which **triggers the real deploy**.

> If you add `approved` before validation is green, **approve-guard removes it**
> and comments why. Wait for green, then approve again.

### Step 6. The deploy runs and the PR merges itself

On approval the pipeline runs pre-deploy guards, deploys to the INT org for real,
**squash-merges the PR**, writes the `deploy-int` commit status and stamps
`deployed:int` on the PR. You do not merge manually.

### Step 7. Promote — just add a label

To move the same change to QA, add the **`deploy:qa`** label to that same PR:

```bash
gh pr edit <pr-number> --add-label deploy:qa
```

The pipeline opens the `feature → qa` PR, checks that INT already deployed this
exact commit, deploys to QA, merges and stamps `deployed:qa`. Then repeat with
`deploy:stage`.

You keep using the **original int PR** as the control surface for the whole chain.

### What a full developer cycle looks like

```
feature/PROJ-123 ──PR──► int      pr-validation ✓ → approve → deploy-int ✓ → merged
                 label deploy:qa    → auto PR → deploy-qa ✓    → merged
                 label deploy:stage → auto PR → deploy-stage ✓ → merged
                                                   (release manager takes it from here)
```

---

## Part 2 — Promote and release

### Promote between environments

Add the `deploy:<env>` label to the int PR. The pipeline will refuse to skip a
step: the **ordering guard** requires a successful `deploy-<previous-env>` status
for that exact commit. A change cannot reach stage without having really deployed
to qa.

Re-adding a label for something already stamped `deployed:<env>` is a safe no-op.

### Release to production — a two-step train

Production is **not** label-driven. It is a deliberate, reviewer-gated train run
from **Actions → Prod Release**.

#### Step 1 — `action: assemble`

Run the workflow with `action = assemble`.

It collects every PR carrying `deployed:stage` that has not already shipped,
creates the release branch (`release/<prefix><n>`) and pushes it. The job summary
lists the stories it gathered.

> **`assemble` does not open the release PR.** It prints a pre-filled compare link
> and stops. Opening the PR is a deliberate human step, so someone reads the list
> of what is about to reach production.

#### Step 2 — open the release PR

Follow the link in the assemble job summary, or:

```bash
gh pr create --base main --head release/<version> --title "Release <version>"
```

Review it — this diff *is* the production change set.

#### Step 3 — authorize the release

Preflight refuses an unauthorized release. Either:

- have **a different user approve** the release PR, or
- add the **`prod-approved`** label to it.

> You cannot approve your own PR on GitHub, so a solo operator must use the
> `prod-approved` label.

#### Step 4 — `action: release`

Run the workflow again with `action = release`.

1. **Preflight** — confirms the release PR exists, is **authorized** and is green.
2. **Validate** — a check-only validation against production in the **ungated**
   `validate_environment`, producing a quick-deploy id. No reviewer is disturbed
   for a run that might fail.
3. **Prod gate** — the job targeting the **gated** `deploy_environment` pauses for
   its required reviewer. This click is the production approval.
4. **Quick-deploy** — deploys the already-validated package (fast, no re-run of
   tests), merges the release PR into `main`, tags the release (`v<version>`),
   publishes a GitHub Release, then replays `main` back onto `int`, `qa` and
   `stage` so every branch agrees again.

> **Why two environments for one deploy?** So the expensive, risky work is
> validated *before* a human is asked to approve anything. See
> [Product_Documentation.md §11](./Product_Documentation.md#11-stage-4--production-release-train).

### After a release — refresh developer sandboxes

Deployments flow forward, so developer sandboxes fall behind. Either refresh the
sandbox from the promotion source org in Salesforce Setup, or cut a branch from
the baseline (**Recreate Feature Branch**) and push it into the sandbox with
**Redeploy Feature Branch** (`dry_run = false`).

Always start the next story from the baseline branch.

---

## Part 3 — Onboard a new project

A consuming repo keeps **no pipeline logic**. It needs only thin caller workflows,
one config file, and its metadata.

### Step 1 — Copy the caller workflows

Copy the eight caller workflows from the reference consumer
(`your-org/sfdx-pipeline-example/.github/workflows/`) into your repo's
`.github/workflows/`:

```
approve-guard.yml          ops-recreate-feature.yml   prod-release.yml
deploy.yml                 ops-redeploy-feature.yml   promote-on-label.yml
pr-validation.yml          ops-rollback.yml
```

Each is a trigger plus a single `uses:` pointing at the central repo. **Do not add
logic to them.** If you find yourself writing steps in a caller, the change
belongs in the central repo.

### Step 2 — Point them at the central pipeline

Every caller must reference the central repo with an explicit ref:

```yaml
jobs:
  validate:
    uses: your-org/sfdx-pipeline-actions/.github/workflows/pr-validation.yml@v1
    secrets: inherit
```

**Pin `@v1`.** It is a moving major tag: it advances on every backward-compatible
release, so you get fixes automatically, while breaking changes ship as `v2` and
you opt in by changing one character. Pin `@v1.0.0` instead to freeze an exact
build.

> Do **not** pin `@main`. Every commit to the central repo would reach your
> production pipeline instantly, with no opt-in and no rollback.

### Step 3 — Add `sf-pipeline/pipeline.config.yml`

This is **the only file you edit per project**. Start from
[`pipeline.config.example.yml`](../sf-pipeline/pipeline.config.example.yml) in the
central repo.

```yaml
source_path: force-app
api_version: "61.0"
node_version: "20"

baseline_branch: main        # the branch representing production
delta_baseline: target       # or: prod

environments:                # your promotion chain, in order; first = entry
  - name: int
    branch: int
    gh_environment: int
    test_level: RunSpecifiedTests
    gate: approval
  - name: qa
    branch: qa
    gh_environment: qa
    test_level: NoTestRun
    gate: approval
  - name: stage
    branch: stage
    gh_environment: stage
    test_level: NoTestRun
    gate: approval

production:
  branch: main
  validate_environment: prod-validate   # ungated
  deploy_environment: prod              # gated — required reviewers
  release_prefix: r

quality_gates:
  pmd: true
  sonar: true
  apex_coverage: true
coverage_threshold: 85

profiles_mode: incremental
require_pr_approval: true
bypass_label: bypass-quality-gates

notifications:
  chat: true
  jira: true
```

Field-by-field semantics are in
[Product_Documentation.md §6](./Product_Documentation.md#6-configuration-reference).

### Step 4 — Set the entry branch in the one `on:` block

GitHub parses `on:` filters before any job runs, so they cannot read the config.
The entry branch therefore appears literally **once**, in `pr-validation.yml`:

```yaml
on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]
    branches: [int]
```

This is the **only** value duplicated outside `pipeline.config.yml`. Every other
caller reads its branch names from the config.

### Step 5 — Create the branches

Create every branch named in the config — e.g. `int`, `qa`, `stage`, `main` — each
pointing at the same starting commit.

### Step 6 — Create the GitHub Environments and secrets

For **each** `gh_environment`, plus both `production` environments, create a
GitHub Environment and add four secrets:

| Secret | Meaning |
|---|---|
| `SF_CONSUMER_KEY` | Connected-app consumer key (client id) |
| `SF_USERNAME` | Integration user for that org |
| `SF_INSTANCE_URL` | Login / My Domain URL |
| `SF_JWT_KEY` | **base64-encoded** `server.key` |

Gating rules:

| Environment | Required reviewers? | Why |
|---|---|---|
| Entry (`int`) | **No** | The gate is the PR approval itself |
| QA / Stage | Optional | Only if you want a second human per promotion |
| `production.validate_environment` | **No** | Check-only; must not disturb anyone |
| `production.deploy_environment` | **Yes** | This click *is* the production gate |

> Can't create these by hand? Run the **Org Bootstrap** workflow. Together with the
> `sf-setup/` kit in the central repo it creates the connected app, the JWT
> certificate and the integration user, and writes the four secrets into the
> GitHub Environment for you.

### Step 7 — Optional repo-level secrets

For notifications: `CHAT_API_TOKEN`, `CHAT_CHANNEL_ID` or `CHAT_WEBHOOK_URL`;
`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`. For Sonar: `SONAR_TOKEN`,
`SONAR_HOST_URL`. Leave them out and those integrations become silent no-ops —
they never fail a run.

### Step 8 — Apply branch protection

Codified and idempotent; needs `gh` authenticated with repo admin:

```bash
REPO=owner/repo bash sf-pipeline/scripts/setup-branch-protection.sh

# strict mode for a real team — also forbids direct commits by admins:
REPO=owner/repo ENFORCE_ADMINS=true bash sf-pipeline/scripts/setup-branch-protection.sh
```

| Branch | Protection applied |
|---|---|
| Entry | PR-only · required approvals · required checks `quality` + `validate` · stale reviews dismissed · conversation resolution · no force-push / delete |
| Downstream + prod | Guard-rails: no force-push / no deletion, plus the required `deploy-<env>` status |

`enforce_admins` defaults to **false** so a solo admin keeps an escape hatch —
GitHub forbids approving your own PR. Set `ENFORCE_ADMINS=true` for a real team.

### Step 9 — Create the labels

`approved`, `deploy:<env>` and `deployed:<env>` for each environment, plus your
`bypass_label`.

### Step 10 — Smoke test

Ship one trivial metadata change (a custom field is ideal — no Apex, so the
coverage gate is skipped) through `int → qa → stage`. Confirm you see: one sticky
PR comment, `pr-validation` success, the approval-triggered deploy, auto-merge,
and `deployed:<env>` stamped at each hop. Stop before prod.

---

## Part 4 — Ops runbooks

All four are `workflow_dispatch` — run them from the **Actions** tab.

### Ops — Recreate Feature Branch

Cut a clean `feature/<name>` from the baseline when a branch is stale or corrupted.

| Input | Notes |
|---|---|
| `name` | Feature name or Jira key; normalised to `feature/<name>` |
| `base` | Optional; defaults to the pipeline baseline |
| `confirm_reset` | Must be `true` to reset an **existing** branch — destructive |

### Ops — Redeploy Feature Branch

Idempotently (re)deploy a feature branch's delta to any environment — to retry a
flaky deploy or re-validate after an org change. **Does not move any env branch**;
this is not a promotion.

| Input | Notes |
|---|---|
| `feature_ref` | The branch or commit to deploy |
| `environment` | int / qa / stage / … |
| `dry_run` | **Defaults to `true`** (check-only). Uncheck for a real deploy |
| `profiles_mode` | One-off override of `full` / `incremental` |

Higher orgs still require their GitHub Environment reviewer.

### Ops — Rollback (revert + redeploy)

Reverts a deployed change and opens a **revert PR into the entry branch**, so the
rollback flows through the same gate → approve → deploy → merge path — fully
validated, reviewed and audited. Nothing is force-deployed.

| Input | Notes |
|---|---|
| `pr_number` | A merged PR to revert (merge commits use `-m 1` automatically) |
| `ref` | Alternatively, a commit SHA |
| `base` | Branch to revert on; defaults to the entry branch. For a prod hotfix rollback, revert on the baseline branch |

A conflicting revert stops with manual `git revert` instructions rather than
guessing.

### Sync env branches

Merges `main` into every env branch. Normally invoked automatically at the end of
a successful release; dispatch it manually if branches have drifted.

---

## Part 5 — Troubleshooting

### The `approved` label keeps disappearing

`approve-guard` removes it because `pr-validation` is not green for the current
commit. Get validation passing, then approve again.

### "Predecessor environment has not deployed this commit"

The ordering guard. The commit you are promoting was never really deployed to the
previous environment. Deploy it there first — a merged PR is not enough, the
`deploy-<previous-env>` **commit status** must exist for that exact SHA.

### A promotion failed before deploying, and re-adding the label does nothing

If the promotion failed while **creating** the env PR (e.g. a transient GitHub
API error), the `deploy:<env>` label is left in place — so re-adding it is a
no-op. **Remove the label, then add it again** to retrigger:

```bash
gh pr edit <pr> --remove-label deploy:stage
gh pr edit <pr> --add-label deploy:stage
```

Nothing was deployed and no branch moved, so this is always safe. The
merge-conflict path *does* remove the label, so there you can simply re-add it.

### Preflight says the release PR "is not authorized"

The production release requires the release PR to be authorized. Either have **a
different user approve** it, or add the **`prod-approved`** label. GitHub forbids
approving your own PR, so a solo operator must use the label.

### The deploy guard reports a merge conflict

Rebase the feature branch on the target env branch and push. The guard runs before
anything touches an org, so nothing partial was deployed.

### The workflow run shows **0 jobs**

Your caller declared a `concurrency` group equal to the reusable workflow's own
(e.g. `pr-validation-<pr-number>`) and cancelled itself. **Remove `concurrency`
from the caller** — concurrency is owned by the reusable workflow.

### The coverage gate did not run

Expected in two cases: the delta contains no `*.cls` / `*.trigger`, or the
environment's `test_level` is `NoTestRun` (qa and stage typically), where there is
no coverage to assess.

### PMD or Sonar did not run

Also expected. The gates fire only when the delta contains the relevant file
types — Apex for PMD, Apex or LWC/Aura JS for Sonar. Sonar additionally needs
`SONAR_TOKEN` and `SONAR_HOST_URL`.

### The bypass label had no effect

Bypass is honoured only if the person who **added** the label has admin or
maintain permission on the repo, re-verified at run time. The label alone is not
authority.

### A validation left a stray deployment record

It shouldn't — `pr-validation`'s `validate-status` job marks the phantom
deployment inactive and deletes it. If you see one, the run failed before that
job. The ordering guard is unaffected: it trusts only the `deploy-<env>` commit
status, which only a real deploy ever writes.

### The QA package differs from the INT package for the same story

Expected when `delta_baseline: target`. Each environment diffs against **its own**
branch, so the package is exactly what that environment is missing. Switch to
`delta_baseline: prod` if you want an identical artifact at every stage.

### chat or Jira posted nothing

They are best-effort, by design — a notification outage must never block a
deployment. Look for a **`chat notification failed`** warning annotation on the
run; it names the cause:

| Warning | Fix |
|---|---|
| `HTTP 401` / `403` | The bot token is invalid, expired, or has no access to the room. Rotate `CHAT_API_TOKEN` |
| `HTTP 404` | Wrong room — check `CHAT_CHANNEL_ID` |
| `could not reach the chat API` | Transient outage or timeout; nothing to do |

The job still succeeds in every case.

> [!NOTE]
> Before **v1.1.0** this was not true: a bad token returned `401`, the notify
> step exited 1, and because notify runs inside the validate job it could **fail
> a production release**. If you see a release fail this way, you are on an older
> pin — move to `@v1`.

### Where do I find the history of what was deployed?

The durable `audit-log` branch:

```bash
git fetch origin audit-log
git show origin/audit-log:audit/audit-log.jsonl | jq 'select(.environment=="prod")'
```

Per-run detail is in the job summary's **🧾 Audit** table and in the
`audit-record.json` inside the `deploy-report-<env>` artifact.

---

## Part 6 — Quick reference

### Labels

| Label | Who adds it | Effect |
|---|---|---|
| `approved` | Reviewer (pipeline stamps it) | Triggers the real entry deploy, then auto-merge |
| `deploy:<env>` | Developer / release manager | Promote this change to `<env>` |
| `deployed:<env>` | Pipeline | Record — this commit really deployed to `<env>` |
| `bypass-quality-gates` | **Admin only** | Skip the quality gates for this PR |

### Commit statuses

| Status | Written by | Means |
|---|---|---|
| `pr-validation` | PR Validation | Check-only validation passed for this commit |
| `deploy-<env>` | A **real** deploy only | This commit is genuinely live in `<env>` |

### Commands

```bash
# open the entry PR
gh pr create --base int --title "PROJ-123: ..." --body "..."

# promote
gh pr edit <pr> --add-label deploy:qa
gh pr edit <pr> --add-label deploy:stage

# branch protection
REPO=owner/repo bash sf-pipeline/scripts/setup-branch-protection.sh

# audit history
git show origin/audit-log:audit/audit-log.jsonl | jq .
```

### Golden rules

1. Branch from the **baseline**, not from `int`.
2. Put the Jira key in the branch name.
3. Never add logic to a caller workflow — it belongs in the central repo.
4. Never set `concurrency` on a caller.
5. Never merge a pipeline PR by hand; the pipeline merges after a successful deploy.
6. `deployed:<env>` is a record, not an instruction — promote with `deploy:<env>`.

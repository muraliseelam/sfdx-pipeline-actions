# Salesforce DevOps Pipeline — User Guide

**Step-by-step instructions for setting up and using the pipeline.** Start at
Part 1 if you are standing it up for the first time; jump to the part that
matches your role otherwise.

> **Want to know how it works under the hood?** See
> **[Product_Documentation.md](./Product_Documentation.md)** — architecture,
> diagrams, branching strategy and every technical detail.

---

## Table of contents

| Part | For | Section |
|---|---|---|
| 1 | **Setting it up the first time** | [Set up the pipeline](#part-1--set-up-the-pipeline-one-time) |
| 2 | Developers | [Ship a change — day to day](#part-2--ship-a-change-day-to-day) |
| 3 | Release managers | [Promote and release](#part-3--promote-and-release) |
| 4 | Anyone on call | [Ops runbooks](#part-4--ops-runbooks) |
| 5 | Everyone | [Troubleshooting](#part-5--troubleshooting) |
| 6 | Everyone | [Quick reference](#part-6--quick-reference) |

Examples use a reference project whose chain is `int → qa → stage → main(prod)`.
**Those names are examples, not requirements** — they come from one config file
and you can call your stages anything. If your chain is `mon → tue → wed → prod`,
write that in `pipeline.config.yml` and the whole pipeline follows.

---

## Part 1 — Set up the pipeline (one time)

**Time: about 30 minutes**, most of it waiting for Salesforce.

You need: a GitHub repo with your Salesforce metadata in it, admin rights on that
repo, and a System Administrator login for each Salesforce org you want to deploy
to.

At the end you will have a working pipeline. You do **not** need to understand the
pipeline to finish this part — just follow the steps in order.

---

### Step 1 — Copy two things into your repo

From this repository, copy into your own repo:

| Copy this | To here |
|---|---|
| `examples/consumer/.github/workflows/` (11 files) | `.github/workflows/` |
| `sf-pipeline/pipeline.config.example.yml` | `sf-pipeline/pipeline.config.yml` |

```bash
# from the root of YOUR repo
git clone --depth 1 https://github.com/muraliseelam/sfdx-pipeline-actions /tmp/sfdx-pipeline
mkdir -p .github/workflows sf-pipeline
cp /tmp/sfdx-pipeline/examples/consumer/.github/workflows/*.yml .github/workflows/
cp /tmp/sfdx-pipeline/sf-pipeline/pipeline.config.example.yml sf-pipeline/pipeline.config.yml
```

Those eleven workflow files are **callers**: each is a trigger plus a single
`uses:` line. They contain no logic, and you should never add any. All the logic
stays in this repository and is versioned here.

---

### Step 2 — Point the callers at this repository

Every caller has one `uses:` line like:

```yaml
uses: muraliseelam/sfdx-pipeline-actions/.github/workflows/pr-validation.yml@v1
```

Replace `muraliseelam/sfdx-pipeline-actions` with wherever this repository actually
lives. One command does all eleven:

```bash
grep -rl 'muraliseelam/sfdx-pipeline-actions' .github/workflows \
  | xargs sed -i '' 's|muraliseelam/sfdx-pipeline-actions|OWNER/REPO|g'   # macOS
# Linux: drop the '' after -i
```

**Keep the `@v1`.** It is a moving major tag: you get fixes automatically, and
breaking changes ship as `v2` so you opt in by changing one character. Never pin
`@main` — every commit here would reach your production pipeline instantly.

---

### Step 3 — Describe your environments

Open `sf-pipeline/pipeline.config.yml`. **This is the only file you edit per
project.** Everything else is generic.

The part that matters is the `environments:` list. Name them whatever your
company calls them — the pipeline does not care. If your chain is
`dev → test → preprod → live`, write exactly that:

```yaml
baseline_branch: main          # the branch that represents PRODUCTION

environments:                  # your promotion chain, IN ORDER. First = entry.
  - name: dev                  # whatever you call it
    branch: dev                # the git branch for this stage
    gh_environment: dev        # the GitHub Environment holding this org's secrets
    gate: auto                 # see the table below
  - name: test
    branch: test
    gh_environment: test
    gate: approval
  - name: preprod
    branch: preprod
    gh_environment: preprod
    gate: on_demand
  - name: live
    branch: live
    gh_environment: live
    gate: on_demand

production:
  branch: main
  validate_environment: prod-validate   # NO required reviewers
  deploy_environment: prod              # WITH required reviewers
  release_branch_prefix: "release/"
```

You can have **1 environment or 10**. Add, remove or rename freely — nothing else
in the pipeline needs to change.

`gate:` decides how each stage is driven:

| `gate:` | What happens | Use it for |
|---|---|---|
| `auto` | A PR into this branch deploys for real and merges itself, no approval | A low-friction dev stage |
| `approval` | A PR runs check-only validation and posts a report; a reviewer approving then deploys and merges | Your main quality stage |
| `on_demand` | Not PR-driven. Deployed when someone adds a `deploy:<name>` label | Later stages like preprod/live |

> **Which `test_level`?** Leave it out and you get `RunSpecifiedTests`, which runs
> only the tests covering what you changed. `NoTestRun` is faster but is rejected
> by Salesforce for production deploys involving Apex.

Full field-by-field reference:
[Product_Documentation.md §6](./Product_Documentation.md#6-configuration-reference).

---

### Step 4 — Set the entry branch (the one extra edit)

Open `.github/workflows/pr-validation.yml` and set the branch filter to your
**first** environment's branch:

```yaml
on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]
    branches:
      - dev          # <-- environments[0].branch from your config
```

This is **the only environment name anywhere outside
`pipeline.config.yml`**. GitHub parses `on:` filters before any job starts, so
this one filter cannot read your config file. Everything else — every other
branch, environment and label — is read from the config at run time.

Commit and push everything so far to your default branch.

---

### Step 5 — Let the pipeline build its own scaffolding

You now need branches, labels and GitHub Environments that match your config.
**Do not create them by hand.** Go to the **Actions** tab, pick **Project Init**,
and click **Run workflow** with *apply* left **unchecked**.

It prints exactly what it would create:

```
1/3  Branches
  ✔ branch dev     (would create, from main)
  ✔ branch test    (would create, from main)
  ...
2/3  Labels
  ✔ label deploy:preprod   (would create)
  ...
3/3  GitHub Environments
  ✔ environment dev        (would create)
```

Read it. If the branch and environment names are the ones you intended, run it
again with **apply checked**. It is idempotent and additive — it never deletes or
moves anything, so re-run it any time you add or rename an environment.

> **"Environments cannot be created here"?** Creating a GitHub Environment needs
> repo *admin*. Either run it as an admin, or add a personal access token with
> admin rights as the `PIPELINE_PAT` repository secret and re-run. Branches and
> labels are still created either way.

Prefer a terminal? The same thing, locally:

```bash
bash /tmp/sfdx-pipeline/sf-pipeline/scripts/init-project.sh --repo OWNER/REPO
# then, once the plan looks right:
bash /tmp/sfdx-pipeline/sf-pipeline/scripts/init-project.sh --repo OWNER/REPO --apply
```

> **New repositories: let Actions open pull requests.** `deploy:<env>` opens the
> feature→`<env>` PR with the workflow token. GitHub turns that off by default, so
> without a `PIPELINE_PAT` (Step 9) every promotion stops with *GitHub Actions is not
> permitted to create or approve pull requests*. Turn on **Settings → Actions → General →
> Workflow permissions → Allow GitHub Actions to create and approve pull requests**.

---

### Step 6 — Connect your Salesforce orgs

Each environment needs four secrets so the pipeline can log in to its org:

| Secret | What it is |
|---|---|
| `SF_CONSUMER_KEY` | Consumer key of a connected app in that org |
| `SF_USERNAME` | The integration user the pipeline logs in as |
| `SF_INSTANCE_URL` | That org's login / My Domain URL |
| `SF_JWT_KEY` | The private key, **base64-encoded** |

**The easy way.** Run the **Org Bootstrap** workflow once per org from the Actions
tab. It creates the connected app, generates the certificate, creates and
configures the integration user, and writes all four secrets into the GitHub
Environment for you. You supply the org's admin login and the environment name.

**The manual way**, if your org restricts connected apps, is in
[`sf-setup/README.md`](../sf-setup/README.md).

Either way, secrets go in **Settings → Environments → `<env>` → Environment
secrets**, *not* repository secrets. That separation is what stops a dev deploy
from ever reaching production credentials.

---

### Step 7 — Put the only real gate on production

**Settings → Environments → your `deploy_environment` (e.g. `prod`) → Required
reviewers → add yourself or a release team.**

That approval click is the production gate. Do this for the production deploy
environment **and nothing else**:

| Environment | Required reviewers? | Why |
|---|---|---|
| Entry (first stage) | **No** | The PR approval is already the gate |
| Middle stages | Optional | Only if you want a second human per hop |
| `validate_environment` | **No** | Check-only; must never block anyone |
| `deploy_environment` | **Yes** | This click *is* the production gate |

---

### Step 8 — Apply branch protection

```bash
REPO=OWNER/REPO bash /tmp/sfdx-pipeline/sf-pipeline/scripts/setup-branch-protection.sh

# for a real team — also stops admins committing straight to a branch:
REPO=OWNER/REPO ENFORCE_ADMINS=true bash .../setup-branch-protection.sh
```

| Branch | What it gets |
|---|---|
| Entry | PR-only, required approval, required `pr-validation` status, no force-push |
| Later stages + prod | No force-push, no deletion |

`ENFORCE_ADMINS` defaults to **false** so a solo admin keeps an escape hatch —
GitHub will not let you approve your own PR.

**Working alone?** That also means the entry branch's required review can never be
met, and the pipeline's merge after a successful deploy is refused. Approve with the
`approved` label instead and drop the review requirement:

```bash
REPO=OWNER/REPO REQUIRED_REVIEWS=0 bash .../setup-branch-protection.sh
```

**Protection and the post-release sync.** After a release the pipeline force-pushes
`main` onto every env branch, which protected branches allow only for an admin token.
Add `PIPELINE_PAT` (Step 9); without it the release still succeeds, but each branch
warns *Could not realign `<branch>`* and has to be synced by hand.

---

### Step 9 — Optional integrations

All of these are **silent no-ops if you skip them**. None can ever fail a deploy.

| Want | Add these repository secrets |
|---|---|
| Chat notifications | `CHAT_WEBHOOK_URL` (Slack, Teams or any webhook — the provider is auto-detected) *or* `CHAT_API_URL` + `CHAT_API_TOKEN` + `CHAT_CHANNEL_ID` |
| Jira story updates | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` |
| Post-release branch sync on protected branches; promotion and rollback PRs that validate without a manual *Approve and run*; promotion across forks | `PIPELINE_PAT` (a PAT with repo **admin**) |

---

### Step 10 — Smoke test before you trust it

Ship one trivial change end to end. **Use a custom field, not Apex** — no Apex
means the coverage gate is skipped, so you are testing the pipeline rather than
your test suite.

```bash
git checkout -b feature/PROJ-1-smoke-test origin/main
# add one custom field under force-app/
git add -A && git commit -m "Smoke test" && git push -u origin HEAD
gh pr create --base dev --title "Smoke test" --body "Verifying the pipeline"
```

Walk it through your whole chain and confirm each of these:

- [ ] `pr-validation` runs and posts **one** sticky PR comment listing the components
- [ ] Approving the PR triggers a real deploy, then an automatic merge
- [ ] A `deployed:<env>` label appears on the PR
- [ ] Adding `deploy:<next env>` promotes it to the next org
- [ ] The component appears in each Salesforce org as it goes

**Stop before production** on the first pass. When all of that works, you are
done — go to [Part 2](#part-2--ship-a-change-day-to-day).

---

### If something goes wrong

Everything in [Part 5 — Troubleshooting](#part-5--troubleshooting), plus the three
mistakes that account for most failed first setups:

| Symptom | Cause |
|---|---|
| Nothing happens when you open the PR | `pr-validation.yml`'s `branches:` filter does not match your entry branch (Step 4) |
| `'xyz' is not a configured environment` | A label or input names an environment that is not in `pipeline.config.yml`. The error lists the valid names |
| Login fails against the org | Secrets are repository secrets rather than **environment** secrets, or `SF_JWT_KEY` was not base64-encoded |

---

### Renaming, adding or removing environments

Do it in `pipeline.config.yml`, then re-run **Project Init**. That is the whole
procedure.

The pipeline has no built-in environment names. `int`, `qa` and `stage` appear
throughout these docs as an example, not a requirement. If your chain is
`mon → tue → wed → thu → prod`, write that:

```yaml
environments:
  - { name: mon, branch: mon, gh_environment: mon, gate: auto }
  - { name: tue, branch: tue, gh_environment: tue, gate: approval }
  - { name: wed, branch: wed, gh_environment: wed, gate: on_demand }
  - { name: thu, branch: thu, gh_environment: thu, gate: on_demand }
production:
  branch: main
  deploy_environment: prod
  validate_environment: prod-validate
```

Everything else follows automatically — your promotion labels become
`deploy:tue`, `deploy:wed`, `deploy:thu`; your commit statuses become `deploy-mon`
and so on; the release train gathers `deployed:thu` because `thu` is your last
stage.

Checklist when you change the chain:

1. Edit `environments:` in `pipeline.config.yml`.
2. Re-run **Project Init** (dry run first). It creates the new branches, labels
   and GitHub Environments. It will **not** delete the old ones — remove those by
   hand once you are sure nothing is mid-flight.
3. Add the Salesforce secrets to each new GitHub Environment (Step 6).
4. If you changed the **first** environment, update the `branches:` filter in
   `pr-validation.yml` (Step 4). This is the only file outside the config that
   ever names an environment.
5. Set required reviewers on the production deploy environment if it changed.

> **A chain can be any length**, including one environment. Nothing assumes three.
> **`name` and `branch` are independent too** — a stage called `qa` can live on a
> branch called `gh-actions-qa`. Set both explicitly if they differ.

---

## Part 2 — Ship a change, day to day

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
## Part 3 — Promote and release

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

Without a `PIPELINE_PAT` the revert PR is opened by the workflow token, and GitHub
doesn't start workflows for it on its own: its PR Validation waits as *Action
required* until a maintainer clicks **Approve and run**.

With `delta_baseline: prod`, reverting a change that never reached production
removes it from the branch but **not** from the env org: relative to production
there is nothing to delete. Delete such components in the org by hand, or use
`delta_baseline: target`.

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

### "GitHub Actions is not permitted to create or approve pull requests"

A `deploy:<env>` label couldn't open the feature→`<env>` PR. Turn on **Settings →
Actions → General → Allow GitHub Actions to create and approve pull requests**, or add
a `PIPELINE_PAT`, then re-add the label. Nothing was deployed and no branch moved.

### "Could not realign `<branch>` — sync it manually" after a release

The release went out; only the follow-up sync of the env branches failed, because
branch protection refuses the workflow token's force-push. Add an admin
`PIPELINE_PAT` and run **Sync Environment Branches**.

### A promotion failed — how do I retry it?

Every failed promotion removes its `deploy:<env>` label and comments the reason on
the control PR, so fix the cause and **add the label again**. A failure before the
deploy (e.g. the env PR couldn't be created) deployed nothing and moved no branch.

### A promotion failed on the coverage gate

On a real deploy the coverage gate is checked **after** Salesforce has accepted the
deploy (Salesforce only requires 75% per class). A change below `coverage_threshold`
therefore reaches that org, but its env PR is not merged, so the org and the branch
disagree. Raise the coverage and re-add the label, or remove the change from the org
by hand. An admin bypass on the control PR does **not** carry into promotions; stages
with `test_level: NoTestRun` skip the gate.

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

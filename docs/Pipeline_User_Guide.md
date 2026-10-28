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

### Step 1 — Copy three things into your repo

From this repository, copy into your own repo:

| Copy this | To here |
|---|---|
| `examples/consumer/.github/workflows/` (11 files) | `.github/workflows/` |
| `examples/consumer/.github/CODEOWNERS` | `.github/CODEOWNERS` |
| `sf-pipeline/pipeline.config.example.yml` | `sf-pipeline/pipeline.config.yml` |

```bash
# from the root of YOUR repo
git clone --depth 1 https://github.com/muraliseelam/sfdx-pipeline-actions /tmp/sfdx-pipeline
mkdir -p .github/workflows sf-pipeline
cp /tmp/sfdx-pipeline/examples/consumer/.github/workflows/*.yml .github/workflows/
cp /tmp/sfdx-pipeline/examples/consumer/.github/CODEOWNERS .github/CODEOWNERS
cp /tmp/sfdx-pipeline/sf-pipeline/pipeline.config.example.yml sf-pipeline/pipeline.config.yml
```

Those eleven workflow files are **callers**: each is a trigger plus a single
`uses:` line. They contain no logic, and you should never add any. All the logic
stays in this repository and is versioned here.

`CODEOWNERS` names who must approve a change to the callers or to
`pipeline.config.yml` — the files that decide what runs with your org
credentials. Replace `@your-org/pipeline-owners` with real users or a team,
ideally two or more people (nobody can approve their own PR). Step 8 makes
GitHub enforce it.

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
| `auto` | Every push to a (non-draft) PR into this branch deploys for real and merges itself, no approval and no PR Validation run | A low-friction dev stage |
| `approval` | A PR runs check-only validation and posts a report; a reviewer approving then deploys and merges | Your main quality stage |
| `on_demand` | Not PR-driven. Deployed when someone adds a `deploy:<name>` label | Later stages like preprod/live |

> **Using `gate: auto`?** Copy the current `deploy.yml` caller from
> `examples/consumer/` — it also fires on `pull_request` opened / reopened /
> synchronize / ready_for_review; an older copy never deploys an auto stage.
> Step 8 gives an auto **entry** branch 0 required reviews, so the pipeline can
> merge it unattended.

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
1/4  Branches
  ✔ branch dev     (would create, from main)
  ✔ branch test    (would create, from main)
  ...
2/4  Labels
  ✔ label deploy:preprod   (would create)
  ...
3/4  GitHub Environments
  ✔ environment dev        (would create)
  ...
  ✔ environment prod — deployments limited to branch 'main' (would set)
  ✔ environment prod-validate — deployments limited to branch 'main' (would set)
4/4  Pull requests opened by GitHub Actions
  ! Actions may not create pull requests, so deploy:<env> needs a PIPELINE_PAT secret.
```

Read it. If the branch and environment names are the ones you intended, run it
again with **apply checked**. It is idempotent and additive — it never deletes or
moves anything, so re-run it any time you add or rename an environment.

**The production environments only accept the production branch.** Your
`deploy_environment` and `validate_environment` hold the production org's
credentials, and a GitHub Environment with no deployment branch rule hands its
secrets to a workflow on *any* branch — so anyone who can push a branch could
read them. Project Init limits both to `production.branch` (the branch
**Prod Release** is run from). It does this to environments that already exist
too, keeping their reviewers; re-run it on an older repo to tighten them. A
`release/*` branch is deliberately not allowed: anyone with write access can push
one.

> **"Environments cannot be created here"?** Creating a GitHub Environment, and
> limiting its branches, needs repo *admin*. Either run it as an admin, or add a
> personal access token with admin rights as the `PIPELINE_PAT` repository secret
> and re-run. Branches and labels are still created either way.

Prefer a terminal? The same thing, locally:

```bash
bash /tmp/sfdx-pipeline/sf-pipeline/scripts/init-project.sh --repo OWNER/REPO
# then, once the plan looks right:
bash /tmp/sfdx-pipeline/sf-pipeline/scripts/init-project.sh --repo OWNER/REPO --apply
```

> **New repositories: let promotions open pull requests.** `deploy:<env>` opens the
> feature→`<env>` PR with `PIPELINE_PAT` when you have one (Step 9), otherwise with
> the workflow token — which GitHub forbids by default, so every promotion stops with
> *GitHub Actions is not permitted to create or approve pull requests*. Either add the
> `PIPELINE_PAT`, or turn on **Settings → Actions → General → Workflow permissions →
> Allow GitHub Actions to create and approve pull requests** (locally, as an admin:
> add `--allow-actions-prs` to the `--apply` run above). That setting also lets a
> workflow *approve* a pull request, and such an approval counts towards the
> required reviews (though never as a code owner's), so on a team repo prefer the
> `PIPELINE_PAT`.

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
tab. It deploys the `SF CICD Deploy` app, generates the certificate, creates and
configures the integration user, and writes all four secrets into the GitHub
Environment for you. Orgs that refuse new Connected Apps (new orgs, since Spring
'26) get the same app as an **External Client App** automatically; the four
secrets are the same either way.

It needs two **repository** secrets before the first run:

| Secret | What it is |
|---|---|
| `SFDX_AUTH_URL` | An sfdx auth URL for a System Administrator of the org you are bootstrapping (`sf org display --verbose --target-org <alias>`). It names **one** org — replace it before you bootstrap the next |
| `BOOTSTRAP_TOKEN` | A personal access token with admin on this repo, so it can write Environment secrets |

You supply the environment name, login URL, integration username and a contact
email. **Delete both secrets once the last org is done**: any workflow a
developer pushes can read repository secrets, and these two are an org admin
login and a repo admin token.

**From a terminal instead** — this is what the verified runs used, and it needs
no secrets in GitHub (a browser opens once per org for the admin login):

```bash
# from the root of YOUR repo, one org at a time
bash /tmp/sfdx-pipeline/sf-setup/scripts/bootstrap-org.sh \
  --org-alias int-admin --instance-url https://test.salesforce.com \
  --repo OWNER/REPO --env int \
  --int-username cicd@acme.com.int --contact-email devops@acme.com
```

`bootstrap-from-config.sh` does every org your config declares in one pass.
Both, and the fully manual route, are in [`sf-setup/README.md`](../sf-setup/README.md).

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

**Prevent self-review.** On the same page, tick **Prevent self-review** when two or
more people can approve: whoever starts the release can then not approve its
production deploy themselves. **Working alone, leave it off** — otherwise nobody
can ever approve your releases.

Both production environments only accept deployments from the production branch
(Step 5), so always run **Prod Release** from that branch — the default in the
*Run workflow* menu.

---

### Step 8 — Apply branch protection

Run it from the root of your repo (it reads your `pipeline.config.yml`), as a repo
admin. Add `DRY_RUN=true` first to print what each branch would get:

```bash
REPO=OWNER/REPO bash /tmp/sfdx-pipeline/sf-pipeline/scripts/setup-branch-protection.sh

# working alone (see below):
REPO=OWNER/REPO REQUIRED_REVIEWS=0 bash .../setup-branch-protection.sh

# for a real team — also stops admins committing straight to a branch:
REPO=OWNER/REPO ENFORCE_ADMINS=true bash .../setup-branch-protection.sh
```

**Every pipeline branch becomes pull-request-only**, so nobody can push straight to
a stage or to production and skip its deploy and guards:

| Branch | What it gets |
|---|---|
| Entry | PR-only; `REQUIRED_REVIEWS` approvals (default 1) and code-owner review; required statuses `pr-validation` (for a `gate: approval` entry) and `deploy-<entry>`; stale approvals dismissed; conversations resolved |
| Later stages | PR-only with no approvals — the pipeline opens and merges these PRs itself; required status `deploy-<env>` |
| Production | PR-only; the entry branch's approvals and code-owner review; no required status |
| All of them | No force-push, no deletion |

The required statuses are the ones the pipeline writes: `pr-validation` after a
green validation, and `deploy-<env>` — named after the environment, not the branch
— only after a **real** deploy, right before it merges. So a PR into a stage can
only merge once its exact commit is live in that org, and the merge button cannot
get ahead of the pipeline. The production branch needs no status because the
release train deploys before it merges. (The jobs' own check runs are named
`<caller job> / <job>` and cannot be required by a fixed name.)

Each required status is **pinned to the GitHub Actions app**, which is what the
pipeline writes them as, so a `deploy-<env>` someone posts with their own token
can't unlock a merge. The default app id, `15368`, is github.com's; on GitHub
Enterprise Server set `STATUS_APP_ID` to your instance's GitHub Actions app id
(`STATUS_APP_ID=""` leaves the statuses unpinned).

**Code-owner review** is what stops a pull request from quietly changing a caller
workflow or `pipeline.config.yml`: with the `CODEOWNERS` from Step 1, such a PR
cannot merge until an owner approves. It follows `REQUIRED_REVIEWS` — on with 1 or
more, off with 0 — because it is still a review someone else has to give. Set
`REQUIRE_CODE_OWNER_REVIEWS=true` with `REQUIRED_REVIEWS=0` to require a review only
for those files.

**Authorizing a release with protection on.** The release PR is merged into
production by the workflow token after the deploy, so it must meet the production
branch's review rule. With `REQUIRED_REVIEWS` 1 or more, authorize it with an
**Approve** review from someone other than its author (a code owner, if it touches
the files in `CODEOWNERS`); the `prod-approved` label on its own is only enough with
`REQUIRED_REVIEWS=0`.

`ENFORCE_ADMINS` defaults to **false** so an admin keeps an escape hatch — and so
an admin `PIPELINE_PAT` can still push the **Sync Env Branches** merge onto a
protected branch. Without that (no PAT, or `ENFORCE_ADMINS=true`) Sync Env Branches
falls back to a sync PR per branch, which an admin has to merge; the post-release
realign below is unaffected, because it lifts protection for its push.

**Working alone?** GitHub will not let you approve your own PR, so the entry
branch's required review can never be met and the pipeline's merge after a
successful deploy is refused. Run it with `REQUIRED_REVIEWS=0` (code-owner review
switches off with it), approve entry PRs with the `approved` label, and authorize
releases with `prod-approved`. Your own `approved` label on your own entry PR is
refused unless you also set `allow_self_approval: true` in `pipeline.config.yml`
**on the entry branch** — the gates are read from the branch a PR targets, and as
an admin you can commit it there directly.

**Protection and the post-release sync.** After a release the pipeline realigns
every env branch with `main` — resetting a branch whose content is all in `main`,
merging `main` into one that still holds unreleased stories — lifting and
restoring each branch's protection with `PIPELINE_PAT` (Step 9). Without it the
release still succeeds, but each branch warns *Could not realign `<branch>`* and
has to be synced by hand. The restore keeps the branch's settings — app-pinned
statuses, review rules, and extras you may add by hand such as last-push approval,
bypass lists or signed commits — and warns if GitHub took only the core rules. If
it can't restore protection at all, that step fails and prints the old settings.

Re-run the script whenever you add or rename an environment.

---

### Step 9 — Optional integrations

All of these are **silent no-ops if you skip them**. None can ever fail a deploy.

| Want | Add these repository secrets |
|---|---|
| Chat notifications | `CHAT_WEBHOOK_URL` (Slack, Teams or any webhook — the provider is auto-detected) *or* `CHAT_API_URL` + `CHAT_API_TOKEN` + `CHAT_CHANNEL_ID` |
| Jira story updates | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` |
| Post-release branch sync and Sync Env Branches on protected branches; promotions without *Allow GitHub Actions to create … pull requests*; promotion, sync and rollback PRs that validate without a manual *Approve and run*; Project Init creating environments | `PIPELINE_PAT` (a PAT with repo **admin**) |

> **`PIPELINE_PAT` is effectively admin for every writer.** Any workflow a
> developer pushes can read repository secrets. Use a fine-grained token limited to
> this one repository — *Administration*, *Contents*, *Pull requests* and
> *Workflows* read and write (the post-release sync pushes commits that may touch
> `.github/workflows/`) — and rotate it. A promotion that fails with *Bad
> credentials* means it expired.

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
| A deploy succeeds but the PR never merges | Branch protection refused the pipeline's merge: a solo maintainer without `REQUIRED_REVIEWS=0`, or a missing code-owner approval (Step 8) |
| Your `approved` label on your own PR is removed | Self-approval of the entry deploy is off: set `allow_self_approval: true` on the entry branch if you work alone (Step 8) |

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
6. Re-run `setup-branch-protection.sh` (Step 8). Each branch's required status is
   `deploy-<environment name>`, so after a rename the old rule waits for a status
   nothing writes any more, and a new branch is unprotected until you do.

> **A config change applies where it lands.** Gates and environments are read from
> the branch a PR targets (if it is protected; otherwise from the default
> branch), never from the PR itself. A change to `pipeline.config.yml` therefore
> applies to PRs into a branch once it has merged into that branch.

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
- runs a **check-only** validation deploy against the INT org — a change without
  Apex too, so broken metadata fails here,
- posts **one sticky comment** with the result,
- writes the `pr-validation` commit status.

Nothing is deployed for real at this point.

> **Changing `pipeline.config.yml` in your PR?** The gates that judge the PR are
> read from `int`, not from your branch, so your change applies only once it has
> merged.

### Step 4. Read the PR comment

The sticky comment is updated in place on every push — there is only ever one.
It tells you which components changed, whether validation passed, and coverage if
Apex was involved.

If it fails, push a fix. Validation re-runs and supersedes the previous run.

### Step 5. Get an approval

Once validation is green, a reviewer with write access **approves** the PR. That
Approve review **triggers the real deploy**. Instead of a review, a repo admin
can add the `approved` label — but not on their own PR, unless the project set
`allow_self_approval: true` (a solo operator, Part 1 Step 8).

> If you add `approved` before validation is green, **approve-guard removes it**
> and comments why. Wait for green, then approve again.

### Step 6. The deploy runs and the PR merges itself

On approval the pipeline runs pre-deploy guards, deploys to the INT org for real,
writes the `deploy-int` commit status, **squash-merges the PR** at exactly the
commit it deployed and stamps `deployed:int` on the PR. You do not merge manually.

The approval is for the commit you had when it was given. **Don't push while it
deploys**: a commit pushed after the approval is refused before the deploy (*Not
deployed to `int` — a new commit was pushed…*), and one pushed during the deploy
leaves the PR unmerged. Either way, wait for validation on the new head and
approve again.

### Step 7. Promote — just add a label

To move the same change to QA, add the **`deploy:qa`** label to that same PR:

```bash
gh pr edit <pr-number> --add-label deploy:qa
```

The pipeline checks that INT deployed **and merged** this exact commit, opens the
`feature → qa` PR, deploys to QA, merges it at the deployed commit and stamps
`deployed:qa`. Then repeat with `deploy:stage` — once `deployed:qa` is there.

You keep using the **original int PR** as the control surface for the whole chain.
Promote only after it has merged, and push nothing more to the feature branch: a
new commit has never been deployed, so it goes through int first (a new PR).

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

Add the `deploy:<env>` label to the **merged** int PR. The pipeline will refuse to
skip a step: the **ordering guard** requires a successful `deploy-<env>` status —
written by the pipeline itself — from **every earlier environment** for that exact
commit, and `deployed:<env>` on the int PR, which says it was merged there too.
`qa` checks `int`; `stage` checks `int` and `qa`. A change cannot reach stage
without having really deployed to both. Each stage deploys the same package
(`main` vs the feature, with the default `delta_baseline: prod`), and its env PR
merges only after that deploy succeeds, at exactly the deployed commit. No
approval is needed on the env PRs.

The promotion is refused — with a comment on the int PR, the label removed, and
nothing deployed — when the label names the entry stage, the int PR isn't merged,
an earlier stage isn't deployed and merged, the feature branch has moved past the
merged commit, or the env PR conflicts. **A conflict is resolved through int:** a
fix pushed to the feature branch is a new commit, so open a new PR into int with
it and promote from that PR once it has merged.

Re-adding a label for something already stamped `deployed:<env>` is a safe no-op.

### Release to production — a two-step train

Production is **not** label-driven. It is a deliberate, reviewer-gated train run
from **Actions → Prod Release**.

#### Step 1 — `action: assemble`

Run the workflow with `action = assemble`. `version` (blank = the ISO
year.week) may use only letters, digits and `. _ -`; anything else is refused.

It collects every story carrying `deployed:stage` that has not already shipped
and that the pipeline really deployed to **every** stage (its own `deploy-<env>`
statuses — a hand-added label isn't enough), creates the release branch
(`release/<prefix><n>`), merges the exact commit each story deployed — never
commits pushed to its branch later — and pushes it. The job summary lists each
story with the commit released. Name stories in `stories` to release only those;
each must still be proven, or the run fails.

Assemble also refuses to ship another story as a passenger. A story branch that
merged or was rebased on `int` (or another env branch) to settle a conflict
carries every story squashed there; one that merged another story's branch
carries that story. The run fails, naming the story and the commits or files
involved, when a gathered commit holds env-branch commits of stories that are not
in this release, or when a story on `hold` would ship anyway. Release the carried
stories in the same train, or rebase the story on `main` and take it through the
stages again.

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

An authorization covers the content it saw: an Approve review counts only for
the commit it approved, and re-assembling the release with new content removes
`prod-approved`.

#### Step 4 — `action: release`

Run the workflow again with `action = release`, from the production branch.

1. **Preflight** — confirms the release PR exists, is **authorized** at its
   current commit, is green, can merge into `main` under its branch protection,
   isn't behind `main` (a hotfix or rollback merged there after assemble would
   otherwise be undone in production), and hasn't been deployed already. Its head
   commit is what gets validated, deployed and merged.
2. **Validate** — a check-only validation against production in the **ungated**
   `validate_environment`, producing a quick-deploy id. No reviewer is disturbed
   for a run that might fail. A release without Apex is validated too (no tests);
   it gets a quick-deploy id only when `destructive_tests` names a canary.
3. **Prod gate** — the job targeting the **gated** `deploy_environment` pauses for
   its required reviewer. This click is the production approval.
4. **Quick-deploy** — deploys the already-validated package (fast, no re-run of
   tests; with no quick-deploy id, a full deploy of the same delta), marks the
   release PR `deployed:prod`, merges it into `main`, tags the release
   (`v<version>`), publishes a GitHub Release, then realigns `int`, `qa` and
   `stage` with `main` so every branch agrees again (a branch with conflicts is
   left as is, with a warning — resolve it with **Sync Env Branches**).

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
| `name` | Feature name or Jira key; normalised to `feature/<name>` — spaces become `-`, characters other than letters, digits, `.`, `_`, `/`, `-` are dropped, and path parts git refuses (empty, `.`, `..`, starting or ending with `.`, ending in `.lock`) are removed — never resolved, so `a/../b` becomes `a/b`, not `b` |
| `base` | Optional; defaults to the pipeline baseline |
| `confirm_reset` | Must be `true` to reset an **existing** branch — destructive |

Resetting an existing branch records its previous tip in the run summary — the
only way back to the discarded commits.

### Ops — Redeploy Feature Branch

Idempotently (re)deploy a feature branch's delta to any environment — to retry a
flaky deploy or re-validate after an org change. **Does not move any env branch**;
this is not a promotion.

| Input | Notes |
|---|---|
| `feature_ref` | The branch or commit to deploy |
| `environment` | int / qa / stage / … |
| `dry_run` | **Defaults to `true`** (check-only). Uncheck for a real deploy |
| `profiles_mode` | `config` (default — use `pipeline.config.yml`), or a one-off `full` / `incremental`; anything else is refused |

Higher orgs still require their GitHub Environment reviewer. The gates and
environments come from the branch you dispatch from only if it is protected;
otherwise from the default branch, so a branch can't relax the coverage gate for
a real deploy.

### Ops — Rollback (revert + redeploy)

Reverts a deployed change and opens a **revert PR into the entry branch**, so the
rollback flows through the same gate → approve → deploy → merge path — fully
validated, reviewed and audited. Nothing is force-deployed.

The revert is cut from the production baseline whenever it applies there, so the
PR carries only the revert and can be promoted like any story. A change that never
reached production can't be reverted on the baseline. Under the default
`delta_baseline: prod` the runbook then **refuses** — before creating a branch or a
PR — because a revert into `base` would deploy and promote every change on `base`
that production doesn't have; it says how to take the change out instead: fix
forward with a new story cut from the baseline, remove its components from the
orgs by hand and revert the commit on the env branches, or use
`delta_baseline: target`. Under `target` the revert is cut from `base` (its package
is computed against `base`, so it stays clean) and the PR says not to promote it.

| Input | Notes |
|---|---|
| `pr_number` | A **merged** PR to revert — an open or closed-unmerged PR is refused, as there is nothing to roll back (merge commits use `-m 1` automatically) |
| `ref` | Alternatively, a commit SHA (7–40 hex characters) |
| `base` | Branch to revert on; defaults to the entry branch. For a prod hotfix rollback, revert on the baseline branch |

Renames are not followed: the revert only touches the files the change touched.
If `base` is behind the baseline, the PR also brings those baseline commits into
`base`, as Sync Env Branches would. A conflicting revert stops with manual
`git revert` instructions (for the baseline, on a branch cut from it) rather than
guessing. The gates and branch names come from a protected branch, so a branch's
own config can't change `delta_baseline` to get past the refusal.

Without a `PIPELINE_PAT` the revert PR is opened by the workflow token, and GitHub
doesn't start workflows for it on its own: its PR Validation waits as *Action
required* until a maintainer clicks **Approve and run**.

With `delta_baseline: prod` a change that never reached production has nothing to
delete relative to production, which is why the runbook refuses it (above).

### Sync env branches

Merges `main` into every env branch. A release realigns the env branches itself,
so dispatch this when branches have drifted, or when that realign left a branch
as is because of conflicts: a conflicting branch gets a PR cut from `main`, which
shows the conflicts to resolve.

---

## Part 5 — Troubleshooting

### The `approved` label keeps disappearing

`approve-guard` removes it because `pr-validation` is not green for the current
commit. Get validation passing, then approve again. The pipeline also removes it,
with a comment, when the person who added it may not approve: on the entry PR the
label needs a repo admin who isn't the PR's author (unless `allow_self_approval:
true`), on a later stage write access. A failed deploy (validation, the coverage
gate, or a quick deploy Salesforce refused) and a deploy the pre-deploy guard
refused (PR closed or moved, conflicts, chain order, validation not green) remove
it too, so adding it again starts a new deploy.

### *Not deployed to `<env>`* — a new commit was pushed after … was approved

The approval was for an earlier commit. Right before the deploy the pipeline saw
a newer head (or a closed PR), so it deployed nothing, merged nothing and removed
`approved`. Once PR Validation is green for the current head, approve again — the
pipeline deploys and merges exactly that commit.

### *Deployed `<sha>` to `<env>`, but this PR was not merged*

The deploy succeeded, but the merge was refused — usually because a commit was
pushed while it deployed (the merge only takes the deployed commit), or because
branch protection refused it (Part 1, Step 8). The org has `<sha>`; the branch does
not, and `deployed:<env>` was not added, so later stages refuse this change. Once
PR Validation is green for the PR's current head, approve again (or re-add
`approved`): that deploys and merges the current head.

### "must be deployed to every earlier stage first"

The ordering guard. The commit you are promoting was never really deployed to one
or more earlier environments; the message lists which. Deploy it there first — a
merged PR is not enough, the `deploy-<env>` **commit status** must exist for that
exact SHA in every earlier environment, written by the pipeline itself (a status
someone posts by hand is ignored). A promotion also needs `deployed:<env>` from
each earlier stage on the int PR: *deployed, but `deployed:<env>` is not on …*
means that stage's merge hasn't happened yet — wait for its promotion to finish,
or fix the refused merge first.

### *`deploy:<entry>` is not a promotion*

The entry stage deploys when its PR is approved, never by a label. Approve the
int PR instead; add `deploy:<env>` labels only for the stages after it, once the
int PR has merged.

### Promotion refused — `<branch>` *has moved on*

The int PR deployed one commit, but the feature branch now has commits after it,
which were never deployed anywhere — the env PR would merge them. Take them
through int first: open a new PR from the feature branch into int, and promote
from that PR once it is deployed and merged. If they don't belong, reset the
branch to the deployed commit and re-add the label.

### "GitHub Actions is not permitted to create or approve pull requests"

A `deploy:<env>` label couldn't open the feature→`<env>` PR. Add a `PIPELINE_PAT`,
or turn on **Settings → Actions → General → Allow GitHub Actions to create and approve
pull requests** (or run `init-project.sh --apply --allow-actions-prs` as an admin),
then re-add the label. Nothing was deployed and no branch moved.

### "Could not realign `<branch>` — sync it manually" after a release

The release went out; only the follow-up sync of the env branches failed —
usually because branch protection refuses the workflow token's push, or the
branch moved while it synced (the push never overwrites that). Add an admin
`PIPELINE_PAT` and run **Sync Environment Branches**. On protected branches that
push also needs `ENFORCE_ADMINS` left at `false`; otherwise it opens a sync PR per
branch for an admin to merge.

### Merging is blocked: *Required status check "deploy-`<env>`" is expected*

Branch protection at work. A PR into an environment branch can merge only once
that exact commit has really been deployed there, and only a real deploy writes
`deploy-<env>` — the check accepts it only from the GitHub Actions app. Don't
merge it by hand: approve the entry PR, or add `deploy:<env>` to the control PR,
and the pipeline deploys and merges it. If you pushed a new commit after the
deploy, that commit has not been deployed — approve or promote again. If you
renamed the environment, re-run `setup-branch-protection.sh`.

### The release deployed, but the release PR did not merge

Production is live; the post-deploy merge into the production branch failed.
Preflight already refuses a release PR that branch protection would block, so
look at the finalize log: usually the release branch changed after preflight (the
merge takes only the commit production runs), or a review was dismissed in
between. Fix that — for a review rule, an **Approve** review from someone other
than the PR's author — then **Re-run failed jobs** on the Prod Release run. That
finishes the merge, the tag, the GitHub Release and the branch sync without
deploying again; it skips a PR that is already merged. Don't run the release
again: preflight refuses a PR that carries `deployed:prod`.

### *Branch "…" is not allowed to deploy to prod* (or `prod-validate`)

Both production environments accept only the production branch (Project Init sets
this). Run **Prod Release** from that branch — the default in *Run workflow*. A
pull request **into** a `release/…` branch hits the same rule: its validation runs
from the pull request's merge ref, which may not use production secrets.
Production is validated by **Prod Release** itself.

### A promotion failed — how do I retry it?

Every failed promotion removes its `deploy:<env>` label and comments on the
control PR why it stopped (Salesforce errors inline) and what the org and the
branch now hold; an env PR the run opened is closed. Fix the cause and **add the
label again**. A failure before the deploy (e.g. the env PR couldn't be created)
deployed nothing and moved no branch. If the fix is new code, it is a new commit:
take it through int first and promote from that PR.

### A promotion failed on the coverage gate

The change was validated with its tests, and the gate stopped it **before**
anything was deployed: the org and the branch still agree. Raising the coverage
is a new commit, so take it through int (a new PR) and promote from that PR. An
admin bypass on the control PR does **not** carry into promotions; stages with
`test_level: NoTestRun` skip the gate.

### Preflight says the release PR "is not authorized"

The production release requires the release PR to be authorized. Either have **a
different user approve** it, or add the **`prod-approved`** label. GitHub forbids
approving your own PR, so a solo operator must use the label. Preflight's other
refusals:

| Message | What to do |
|---|---|
| *approved, but not at its current commit* | The release branch changed after the review. Approve again, or add `prod-approved` |
| *can't merge into `main` yet — branch protection blocks it* | Usually a required review: the label alone doesn't meet it (Part 1, Step 8). Nothing was deployed |
| *conflicts with `main`* | Assemble the release again |
| *N commit(s) behind `main`* | Something merged into `main` after assemble. Assemble the same version again (it merges `main` in and removes `prod-approved`), authorize it again, then release. The deploy job refuses the same way if `main` moves while it waits for approval |
| *carries `deployed:prod`* | That release is already in production. Re-run the earlier run's failed finalize job instead |

### The deploy guard reports a merge conflict

For an entry PR, rebase the feature branch on the target env branch and push,
then approve again. The story then carries every change on that branch that
production doesn't have yet, so it can only be released together with those
stories — assemble refuses it otherwise. For a promotion, the fix is a new commit: take it through int
first and promote from that PR. The guard runs before anything touches an org, so
nothing partial was deployed. *GitHub has not finished checking whether this PR
can merge* is the same guard: try again in a minute.

### *Deletions skipped — not in `<env>`*

Informational. The change deletes components that org no longer has (already
deleted, or never deployed there), so there was nothing to delete; Salesforce
would otherwise fail the whole deploy. The report lists them. If nothing else was
left, the deploy succeeds without deploying anything.

### *Salesforce refused the quick deploy …*

A validated job can be quick-deployed once, within 10 days, if it ran Apex tests
and the org hasn't changed under it — another deploy may have landed between the
validation and the quick deploy. Nothing was deployed. For a promotion, re-add the
label; for an entry deploy, add `approved` again (a failed deploy removes it) or
approve again — it validates afresh. For a release, check **Setup → Deployment Status** in
production, then run **Prod Release** with `action: release` again: it validates
again and asks for approval again. There is no automatic fallback.

### *… was still running in Salesforce when the wait ran out*

The job may still complete in the org. Check **Setup → Deployment Status** before
retrying, so you don't deploy twice. For a release whose deploy did complete, the
job summary lists how to finish by hand (label, merge, tag, sync).

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
authority. A push after the label was added removes it (like a stale review); an
admin re-adds it for the new head.

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
| `approved` | Entry PR: a repo admin who isn't its author (unless `allow_self_approval`). Later stages: anyone with write access | Like an Approve review: triggers the real deploy, then the merge |
| `deploy:<env>` | Developer / release manager, on the merged int PR | Promote this change to `<env>` |
| `deployed:<env>` | Pipeline, after the merge | Record — this commit was deployed to `<env>` and merged |
| `bypass-quality-gates` | **Admin only** | Skip the quality gates for this PR (dropped by a later push) |
| `prod-approved` | Release manager, on the release PR | Authorizes the production release |
| `deployed:prod` | Pipeline, on the release PR | Production runs this release — a second run is refused |

### Commit statuses

Both are written by the pipeline as `github-actions[bot]`; a status anyone else
posts is ignored.

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

# branch protection — from the root of your repo
REPO=owner/repo bash /path/to/sfdx-pipeline-actions/sf-pipeline/scripts/setup-branch-protection.sh

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
7. Once a PR is approved or merged, don't push to its branch: a new commit takes a
   new approval, and after the int merge a new PR into int.

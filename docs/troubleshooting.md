# Troubleshooting

Start with `python sf-pipeline/scripts/bootstrap.py doctor` (local) and **Actions → SF Pipeline
Doctor** (from GitHub). Between them they catch most setup problems and print the fix.

## Setup (your machine)

| You see | Why | Fix |
|---|---|---|
| **PyYAML is not installed for this Python (…)** | `pip` installed it for a different Python than the one running the script | Run the exact command it prints (`"<that python>" -m pip install --user pyyaml`), then run the script again with that same Python |
| **`sf` not found on PATH** | The Salesforce CLI isn't installed for the shell you're using | `npm install --global @salesforce/cli`, then open a new terminal |
| `npm.ps1 cannot be loaded because running scripts is disabled` (Windows PowerShell) | PowerShell's execution policy blocks npm's `.ps1` wrapper | Call the `.cmd` wrapper instead: `npm.cmd install --global @salesforce/cli` (the scripts find `sf.cmd` by themselves) |
| `gh` errors (HTTP 401/403) from `bootstrap.py` | GitHub CLI isn't logged in, or lacks repo admin | `gh auth login`, then `gh auth status` should show the `repo` and `workflow` scopes |

## Pull requests

| You see | Why | Fix |
|---|---|---|
| **Missing secret(s) for dev: SF_DEV_AUTH_URL** | The org's secret isn't set (or isn't visible: org secrets must be repository secrets, production's environment secrets) | `bootstrap.py secrets --env dev` after `sf org login web --alias dev` |
| **Couldn't log in to the `dev` org** comment | Secret missing, expired or revoked | Same as above, then run SF Pipeline Doctor and re-run the check |
| **PR routing:** feature PRs must target 'dev' | The PR targets a later env | Retarget it to the first env; later envs get it via `promote:<env>` |
| **PR routing:** '…' is an environment branch | A PR from one env branch into another | Promote individual changes with labels instead |
| **PR routing:** 'main' only accepts release/* PRs | A PR straight into production | Releases come from SF Prod Release |
| **Meta-pair check:** X.cls has no X.cls-meta.xml | A source file without its metadata file (or the reverse) | Add the missing file |
| **Branch origin check:** the branch carries commits that exist only on 'int' | The branch was cut from an env branch, not from `main` | `git switch -c fix origin/main` and cherry-pick your commits |
| **Missing Apex test (ApexClass X)** | No `XTest`/`X_Test`/… `@isTest` class exists | Add one, or map it under `tests.overrides` |
| **Coverage gate** ❌ in a passing validation | A changed class is below `coverage_threshold`, or no test executes it (0%) | Improve the test, or exempt a class without executable lines via `quality.coverage_exempt` |
| **Code analysis found blocking violations** | Code Analyzer findings at/above `severity_threshold` | Fix them, or tune `quality.code_analyzer` |
| Checks never start on a PR | The PR has merge conflicts (GitHub doesn't run `pull_request` workflows then) | Resolve the conflicts |
| `sf-approval` pending after adding the label | The label was added while the commit was still being pushed (new commits remove it) | Add the label again once the checks have started |
| Approved PR doesn't merge | Auto-merge needs `SF_PIPELINE_PAT`, and it waits for green checks | Set the PAT, or merge (squash) by hand when green |
| **Couldn't enable auto-merge on PR #N** warning | Another PR kept changing the same branch while it was retried | Merge it (squash) by hand once it's green |
| **⛔ `promote:<env>` was not applied** comment | Promotion order is enforced: each env promotes only to the next one, and production is released, not promoted | Label the PR that merged into the env just before `<env>` (the promotion PR, not the original) |

## Deploys

| You see | Why | Fix |
|---|---|---|
| **Approval guard:** … is not associated with a pull request | A direct push to an env branch | Env branches only take PRs, which branch protection enforces |
| Deploy skipped: no changes under force-app | The merge changed no metadata | Nothing to do |
| Deploy skipped: branch is identical to main | The branch was just created or re-synced | Nothing to do |
| **SourceConflictError** | An old pipeline version on a source-tracked org | Current versions pass `--ignore-conflicts`; update `.github/actions` |
| A `deploy:<env>` label did nothing | The PR isn't approved, or it's closed | Approve it, then add the label again (it's removed after every run) |
| **Deletions skipped (already gone from `<org>`)** in a report | The org no longer has components the change deletes | Nothing; the deletes were already done |
| **No `<Type>` named: X found** | A delete of something the org doesn't have, which the pipeline couldn't rule out (a folder-based type, a type the org can't list, or an older pipeline version) | Remove the member from the change, or delete it in the org by hand first; on an old version, update `.github/actions` and `sf-pipeline/scripts` |

## Releases

| You see | Why | Fix |
|---|---|---|
| Release PR shows **deploy-prod pending** | Expected. It turns green only when production has the release | Add `approved` after the production validation passed |
| **⛔ Not deployed: hasn't been validated** | The label was added before the prepare run finished | Wait for *Validated against production*, then add the label again |
| **⛔ Not deployed: the release branch changed after validation** | Someone pushed to `release/*` | Re-run SF Prod Release (prepare) with the same version |
| **Production validation failed** | Tests or components fail against production | Fix on the source env (via the normal flow), then re-run prepare with the same version (the PR is refreshed) |
| **Quick deploy unavailable** warning | The validation is older than 10 days, or production changed since | Nothing; the same artifact was deployed in full |
| **Quick deploy failed** (any other error) | Possibly still running in the org (e.g. a wait timeout) | Check *Setup → Deployment Status*, then run SF Prod Release (deploy) again; it never deploys twice |
| **Merge failed** after deploying | e.g. token expired | Fix it, then run SF Prod Release with `action=deploy`; it finishes without redeploying |
| Labeling the release PR does nothing | The release PR has merge conflicts | Resolve them (or re-run prepare) |
| **Tag vX already exists; choose another version** | That version was released already | Run prepare with a new version (or leave it blank for a date-based one) |
| **PR #N is already released (vX); nothing to do** | A deploy re-run for a finished release | Nothing; re-runs never deploy twice |

## Env-branch sync

| You see | Why | Fix |
|---|---|---|
| **Protection NOT restored on `<branch>`** | The restore call failed | Download the `branch-protection-<env>` artifact, then `branch_protection.py restore --branch <b> --snapshot <file>` (or `bootstrap.py github`) |
| Commits listed as "not in main" | Unreleased work that was on the env branch | Re-promote those changes if they're still wanted |
| PRs conflict after a direct change to pipeline files on `main` | Env branches still hold older copies | Run **Sync Env Branches** (or wait for the next release) |

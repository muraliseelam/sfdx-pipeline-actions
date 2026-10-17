# Build & end-to-end test checklist

The checklist used to turn the pipeline into an open-source template and prove it end to end
against real Salesforce orgs (five scratch orgs from a Dev Hub) and real GitHub Actions runs.
Each item was ticked only after it was verified. PR and run numbers refer to this repository.

Sections A, B and R were first done in `salesforce-devops-actions`, whose history is merged here;
section U combines it with the original `sfdx-pipeline-actions`. Sections C to E ran in this repo.

Legend: `[x]` done and verified · `[~]` partly verified (what's left is noted) · `[ ]` to do · `[!]` needs the repo owner

## U. Unification with the original sfdx-pipeline-actions
- [x] U0 Histories merged; single-repo pipeline is the base; reusable-workflow layer, consumer examples and personalization scripts removed; MIT license kept
- [x] U0b Repo made public; `bootstrap.py github` applied (branches, labels, environments, protection)
- [x] U1 Chat notifications: Slack / Teams / Webex / generic webhook / token API (from notify.sh)
- [x] U2 Jira: comment + transition the story in the branch name on each deploy (per-env `jira_status`)
- [x] U3 Durable audit trail: one JSON record per real deploy appended to an `audit-log` branch
- [x] U4 Destructive-delete dependency lint in the deploy action
- [x] U5 Profiles / permission sets: `full` or `incremental` deploys
- [x] U6 Admin bypass label for the coverage + code-analysis gates (admin/maintain verified at run time)
- [x] U7 Rollback runbook: revert a merged PR/commit as a PR that flows through the pipeline
- [x] U8 Integration user: documented (dedicated user + profile pre-authorization via `jwt-setup --profile`); not automated: External Client Apps pre-authorize by profile, and creating users consumes org licenses
- [x] U9 Self-test runs the link checker; 66 tests cover every ported feature (green on GitHub Linux runners, nothing skipped)

## A. Open-source architecture
- [x] A1 Convention-over-configuration: env list is the only required input; branch, alias, secret names, status check, GitHub environment, instance URL and test policy all default from the env name; last env = production — unit-tested; live config resolves 5 envs from names only
- [x] A2 Promotion order = `environments` order (works for 2-6 stages; 4 and 5 tested) — unit-tested for 2/4/5 stages
- [~] A3 Two auth methods: `sfdx_url` (default, no Connected App) and `jwt` (External Client App / Connected App); per-env override — sfdx_url verified on GitHub for all five orgs (every run below); jwt login verified locally against a scratch org (A17); a JWT run on GitHub needs the owner to run `bootstrap.py jwt-setup` (it writes secrets)
- [x] A4 Missing-secret errors name the exact secret and the command that creates it — verified on GitHub in the earlier repo (the error names SF_DEV_AUTH_URL and the bootstrap command)
- [x] A5 Approval model: `approved` label **or** N reviews → `sf-approval` status, required by branch protection — PR #1: label → `sf-approval` "Approved via the 'approved' label" → auto-merge
- [x] A6 `approved` label is removed when new commits are pushed (configurable) — PR #3 (D13)
- [x] A7 Approval guard on real deploys uses the same rule (label or reviews) — PR #6: `deploy:qa` without approval → guard failed, label removed; every merge deploy passed it
- [x] A8 Two-phase release: dispatch → assemble + validate → `approved` label on the release PR → deploy → merge → tag → release → sync — v1.1.0 (PR #16), v1.1.1 (#22), v1.2.0 (#26), v1.2.1 (#30)
- [x] A9 `bootstrap.py init` — generate the config from env names — unit-tested (rewrites only the environments block)
- [x] A10 `bootstrap.py github` — branches, labels, merge settings, Actions permissions, environments, protection (idempotent) — applied to the live repo; second run reports 24/24 ok (idempotent)
- [x] A11 `bootstrap.py secrets` — push auth secrets from locally logged-in orgs (auth URL or JWT) without printing them — the owner's run set all 7 secrets (4 repo + prod in 2 environments + PAT)
- [x] A12 `bootstrap.py doctor` — config, branches, labels, environments, protection, secret names — live run: all GitHub checks ok, flags exactly the 6 missing secrets with fixes
- [x] A13 SF Pipeline Doctor workflow — logs in to every org from GitHub and reports — run 39026090298: five logins + token check green
- [x] A14 Pipeline self-test workflow — unit tests + actionlint on pipeline changes — green on PRs and on pushes to `main` (71 tests)
- [x] A15 Scratch-org definition for trying the pipeline without sandboxes — config/project-scratch-def.json; used to create the 5 E2E orgs
- [x] A16 Code analysis gate (Salesforce Code Analyzer on changed files) — PR #1: Low findings listed, nothing blocking; PR #7: a High finding (`AvoidGetHeapSizeInLoop`) blocked a PR whose org validation passed
- [x] A17 One-command JWT (`bootstrap.py jwt-setup`): metadata-deployed External Client App + verified JWT login against a scratch org

## B. Local verification
- [x] B5 Real-org behaviours: quick deploy (new job ID; reuse → CannotQuickDeployError), destructive-only deploy with empty package.xml, NoTestRun dry-run is check-only; found SourceConflictError on tracked orgs → `--ignore-conflicts`
- [x] B1 Unit tests cover config defaults, 4/5-stage chains, auth, approvals, bootstrap planning — 50 tests
- [x] B2 actionlint + shellcheck clean (workflows + composite actions) — lint.py: 8 workflows + 15 composite blocks clean
- [x] B3 Real `sf` JSON captured from scratch orgs (success, test failure, coverage, compile error) and used as report fixtures — 5 fixtures from real sf 2.153 output; found that failed `deploy validate` returns only data.deployId → action now fetches `deploy report`
- [x] B4 Local auth-URL login round trip works through the same commands sf-setup runs — stdin login round trip OK; found `--sfdx-url-stdin` needs `-`

## R. Independent runtime reviews (fixed + re-tested)
- [x] R1 Release PR mergeable while awaiting approval → deploy-prod stays pending until production has the release
- [x] R2 Double prod deploy on re-run after a failed merge → `deployed=<sha>` marker; resolve picks deploy/finalize/finish/done
- [x] R3 Forgeable PR-body marker → deploy trusts only the `sf-release-validation` status posted by github-actions
- [x] R4 sf-approval leaking between promotion PRs sharing a head SHA → unique promotion commit per PR + open-PR-only approvals
- [x] R5 Prepare/deploy overlap and stale validation → one concurrency group; prepare resets marker; missing status = failed
- [x] R6 Quick-deploy fallback on timeout could double deploy → fall back only on CannotQuickDeployError
- [x] R7 Admin PAT readable from any branch → optional `admin_pat_secret`, sync runs in the protected validation environment
- [x] R8 Auto-created unprotected environments never repaired → bootstrap repairs policy/reviewers; doctor checks them
- [x] R9 Post-merge race in commit→PR lookup → retry + `(#N)` subject fallback
- [x] R10 Smaller: dispatch on baseline ref, CRLF-safe secret piping, case-insensitive labels, annotation escaping, non-string env keys, compare-API failure message, PR validation uses default-branch pipeline files

## C. GitHub bootstrap
- [x] C0 Env-branch reset (sync logic) run locally after direct pipeline pushes to main: 4 branches reset, protection restored + verified each time
- [x] C1 Pipeline committed and pushed to `main`
- [x] C2 `bootstrap.py github` applied to the live repo — branches, labels, environments, protection live
- [x] C3 Secrets set by the repo owner (`bootstrap.py secrets`)
- [x] C4 Doctor workflow green for all five orgs — run 39026090298

## D. End-to-end scenarios (5 stages: dev → int → qa → stage → prod)
- [x] D1 Happy path: PR to dev with Apex + test → report → `approved` label → squash merge → deploy to dev org (verified in org) — PR #1: RunSpecifiedTests `E2eTierServiceTest`, 100% coverage; classes in the dev org; audit record written
- [x] D2 Changed class without a test → fail fast, synthetic report, nothing deployed — PR #2: lists the five test names it looked for
- [x] D3 Coverage below 85% → coverage gate fails — PR #3: `E2eLowCoverage` 80.0% ❌ while the org validation passed
- [x] D4 Failing Apex test → test failures table — PR #4: assertion message + stack trace
- [x] D5 Compile error → component failures table — PR #5: `Variable does not exist: missingVariable` at 3:16, plus the dependent test class
- [x] D6 Missing `-meta.xml` → preflight fails — PR #31: the comment quotes "E2eNoMeta.cls has no E2eNoMeta.cls-meta.xml companion"
- [x] D7 Branch cut from an env branch → preflight fails — PR #19: "the branch carries 1 commit(s) that exist only on 'int'"
- [x] D8 Feature PR straight into int → routing fails — PR #32: the comment says to use `promote:int`
- [x] D9 Non-Apex metadata → NoTestRun dry-run validation — PR #6: custom label validated with NoTestRun, then deployed to dev
- [x] D10 Deleted metadata → destructiveChanges deployed — PR #17 (rollback of v1.1.0): "0 to deploy, 3 to delete"; classes and label gone from the dev org
- [x] D11 Pipeline-only change → gate passes with "no Salesforce changes" — PR #33: `deploy-dev` green "No Salesforce metadata changes"
- [x] D12 `deploy:<env>` label → pre-merge deploy, label removed — PR #7 (approved, blocked by analysis): `deploy:qa` deployed its head into the qa org; label removed; deploy report on the PR
- [x] D13 New push removes the `approved` label and blocks merge until re-approved — PR #3
- [x] D14 Promote dev → int → qa → stage via labels, validated and deployed at each step — PRs #1 → #8 → #9 → #11, and #6 → #10 → #14 → #15; verified in every org
- [x] D15 Release dry run (PR stays unmergeable) — PR #16: `deploy-prod` pending, `sf-release-validation` recorded the job, PR blocked
- [x] D16 Release: validate → `approved` → quick deploy → merge (SHAs kept) → tag → GitHub Release — v1.1.0: RunLocalTests validation, quick deploy, merge commit with both parents, tag + Release
- [x] D17 Post-release sync: env branches reset, protection restored exactly, unreleased commits listed — all four branches at the release commit; the two unreleased dev commits (#12, #13) were the ones listed
- [x] D18 Quick-deploy fallback to full deploy — v1.1.1: the validated job was used outside the pipeline → CannotQuickDeployError → full deploy (found and fixed: deletions already gone; see X3)
- [x] D19 Concurrent merges to one env don't collide — PRs #12 + #13 approved together: both deployed (found and fixed: X1)
- [x] D20 Re-runs are idempotent (promote, release with same version) — re-labeling `promote:qa` kept one promotion PR; deploy re-run on a released PR: "already released; nothing to do"; prepare with a used version: "Tag v1.1.1 already exists"
- [x] D21 Cleanup release removes the test metadata from production (destructive in prod) — v1.1.1 (SF Rollback of v1.1.0) and v1.2.1 (rollback of v1.2.0); production has none of the test classes

## E. 4-stage variant (int → qa → stage → prod)
- [x] E1 Switch config to 4 envs, re-bootstrap, PR → int → promote → release — `bootstrap.py github` had nothing to change; PR #23 entered at int → #24 → #25 → v1.2.0 in production; the sync skipped `dev`; rolled back through #27–#30 (v1.2.1)
- [x] E2 Restore the 5-stage config — sync realigned all four env branches

## X. Fixed during the end-to-end run
- [x] X1 Auto-merge raced a concurrent merge ("Base branch was modified") and gave up → retried
- [x] X2 Release prepare read GitHub's 404 body as an existing release branch → only a successful lookup counts
- [x] X3 A full-deploy fallback failed on deletions the org no longer had ("No ApexClass named X found") → such deletions are checked against the org, skipped and listed
- [x] X4 Unit tests read the team's own config, so renaming environments broke the self-test → tests use a fixed five-stage fixture
- [x] X5 Rejected promotion labels only failed in the Actions log → the PR gets a comment and the label is removed
- [x] X6 Preflight failures said "see the run" → the PR comment quotes each problem
- [x] X7 Post-merge deploys reported only in the job summary → the deploy report is added to the merged PR
- [x] X8 Rollback commits were authored by a bot → they carry the name of whoever ran the rollback
- [x] X9 Windows: scripts crashed printing ✅ on a cp1252 console, couldn't run `sf.cmd`, and a missing PyYAML gave a stack trace → UTF-8 output, `shutil.which`, and a message with the exact install command
- [x] X10 Coverage gate said "no Apex tests ran" on a deletion-only release that ran 7 tests → "no changed Apex to gate"; the audit record no longer calls an empty fallback a quick deploy

## F. Documentation & open-source polish (after testing)
- [x] F1 README quick start (env names → bootstrap → secrets → first PR)
- [x] F2 Docs: getting started, configuration reference, authentication, approvals, workflows, troubleshooting — updated with everything the end-to-end run showed
- [x] F3 CONTRIBUTING, SECURITY, CHANGELOG, issue + PR templates
- [ ] F4 Repo marked as a template, description + topics
- [ ] F5 Publish the documentation
- [ ] F6 Final cleanup (test branches/PRs closed, scratch orgs listed for the owner)

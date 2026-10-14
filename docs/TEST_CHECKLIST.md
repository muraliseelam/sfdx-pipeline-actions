# Build & end-to-end test checklist

Working checklist for turning the pipeline into an open-source, zero-friction template and proving
it end to end against real Salesforce orgs (5 fresh scratch orgs from the `partner-pbo` Dev Hub) and
real GitHub Actions runs. Items are worked top to bottom; each is ticked only after it's verified.

Sections A–D and R were done in `salesforce-devops-actions`, whose history is merged into this repo.
Section U combines it with the original `sfdx-pipeline-actions`; the E2E scenarios (C3 onward) now run here.

Legend: `[x]` done and verified · `[~]` in progress · `[ ]` to do · `[!]` blocked (reason noted)

## U. Unification with the original sfdx-pipeline-actions
- [x] U0 Histories merged; single-repo pipeline is the base; reusable-workflow layer, consumer examples and personalization scripts removed; MIT license kept
- [x] U0b Repo made public; `bootstrap.py github` applied (branches, labels, environments, protection)
- [x] U1 Chat notifications: Slack / Teams / Webex / generic webhook / token API (from notify.sh)
- [x] U2 Jira: comment + transition the story in the branch name on each deploy (per-env `jira_status`)
- [x] U3 Durable audit trail: one JSON record per real deploy appended to an `audit-log` branch
- [x] U4 Destructive-delete dependency lint in the deploy action
- [x] U5 Profiles / permission sets: `full` or `incremental` deploys
- [x] U6 Admin bypass label for the coverage + code-analysis gates (admin/maintain verified at run time)
- [ ] U7 Rollback runbook: revert a merged PR/commit as a PR that flows through the pipeline
- [ ] U8 Optional dedicated integration user + least-privilege permission set in `jwt-setup`
- [ ] U9 Self-test runs the link checker; tests cover every ported feature

## A. Open-source architecture
- [x] A1 Convention-over-configuration: env list is the only required input; branch, alias, secret names, status check, GitHub environment, instance URL and test policy all default from the env name; last env = production — unit-tested; live config resolves 5 envs from names only
- [x] A2 Promotion order = `environments` order (works for 2-6 stages; 4 and 5 tested) — unit-tested for 2/4/5 stages
- [~] A3 Two auth methods: `sfdx_url` (default, no Connected App) and `jwt` (External Client App / Connected App); per-env override — sfdx_url + jwt coded; sfdx_url login verified locally; GitHub run pending secrets
- [x] A4 Missing-secret errors name the exact secret and the command that creates it — verified on GitHub: PR #4 error names SF_DEV_AUTH_URL + the bootstrap command
- [x] A5 Approval model: `approved` label **or** N reviews → `sf-approval` status, required by branch protection — verified on GitHub: PR #1 `approved` label → sf-approval success → merge allowed
- [ ] A6 `approved` label is removed when new commits are pushed (configurable)
- [~] A7 Approval guard on real deploys uses the same rule (label or reviews) — coded; E2E pending
- [~] A8 Two-phase release: dispatch → assemble + validate → `approved` label on the release PR → deploy → merge → tag → release → sync — coded; E2E pending
- [x] A9 `bootstrap.py init` — generate the config from env names — unit-tested (rewrites only the environments block)
- [x] A10 `bootstrap.py github` — branches, labels, merge settings, Actions permissions, environments, protection (idempotent) — applied to the live repo; second run reports 24/24 ok (idempotent)
- [~] A11 `bootstrap.py secrets` — push auth secrets from locally logged-in orgs (auth URL or JWT) without printing them — dry run reads all 5 auth URLs; real run is the owner's
- [x] A12 `bootstrap.py doctor` — config, branches, labels, environments, protection, secret names — live run: all GitHub checks ok, flags exactly the 6 missing secrets with fixes
- [~] A13 SF Pipeline Doctor workflow — logs in to every org from GitHub and reports — workflow written; needs secrets
- [x] A14 Pipeline self-test workflow — unit tests + actionlint on pipeline changes — green on PRs #1-#4
- [x] A15 Scratch-org definition for trying the pipeline without sandboxes — config/project-scratch-def.json; used to create the 5 E2E orgs
- [x] A16 Code analysis gate (Salesforce Code Analyzer on changed files) — verified on GitHub, PR #4: report comment, Low findings listed, nothing blocking
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
- [x] C1 Pipeline committed and pushed to `main` — 3cec2ea + follow-ups
- [x] C2 `bootstrap.py github` applied to the live repo — branches, labels, environments, protection live
- [!] C3 Secrets set by the repo owner (`bootstrap.py setup`) — needs the owner, now on sfdx-pipeline-actions
- [ ] C4 Doctor workflow green for all five orgs

## D. End-to-end scenarios (5 stages: dev → int → qa → stage → prod)
- [ ] D1 Happy path: PR to dev with Apex + test → report → `approved` label → squash merge → deploy to dev org (verified in org)
- [ ] D2 Changed class without a test → fail fast, synthetic report, nothing deployed
- [ ] D3 Coverage below 85% → coverage gate fails
- [ ] D4 Failing Apex test → test failures table
- [ ] D5 Compile error → component failures table
- [x] D6 Missing `-meta.xml` → preflight fails — PR #2: preflight names E2eNoMeta.cls; report comment posted
- [ ] D7 Branch cut from an env branch → preflight fails
- [x] D8 Feature PR straight into int → routing fails — PR #3: routing error tells the user to use promote:int
- [ ] D9 Non-Apex metadata → NoTestRun dry-run validation
- [ ] D10 Deleted metadata → destructiveChanges deployed
- [x] D11 Pipeline-only change → gate passes with "no Salesforce changes" — PR #1: deploy-dev green 'No Salesforce metadata changes'; merge → env deploy skipped (no force-app changes)
- [ ] D12 `deploy:<env>` label → pre-merge deploy, label removed
- [ ] D13 New push removes the `approved` label and blocks merge until re-approved
- [ ] D14 Promote dev → int → qa → stage via labels, validated and deployed at each step
- [ ] D15 Release dry run (PR stays unmergeable)
- [ ] D16 Release: validate → `approved` → quick deploy → merge (SHAs kept) → tag → GitHub Release
- [ ] D17 Post-release sync: env branches reset, protection restored exactly, unreleased commits listed
- [ ] D18 Quick-deploy fallback to full deploy
- [ ] D19 Concurrent merges to one env don't collide
- [ ] D20 Re-runs are idempotent (promote, release with same version)
- [ ] D21 Cleanup release removes the test metadata from production (destructive in prod)

## E. 4-stage variant (int → qa → stage → prod)
- [ ] E1 Switch config to 4 envs, re-bootstrap, PR → int → promote → release
- [ ] E2 Restore the 5-stage config

## F. Documentation & open-source polish (after testing)
- [ ] F1 README quick start (env names → bootstrap → secrets → first PR)
- [ ] F2 Docs: getting started, configuration reference, authentication, approvals, workflows, troubleshooting, FAQ
- [ ] F3 CONTRIBUTING, SECURITY, CHANGELOG, issue + PR templates
- [ ] F4 Repo marked as a template, description + topics
- [ ] F5 Publish the documentation
- [ ] F6 Final cleanup (test branches/PRs closed, scratch orgs listed for the owner)

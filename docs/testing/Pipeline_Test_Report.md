# Salesforce CI/CD Pipeline — Adversarial Test Report

**System under test:** `muraliseelam/sfdx-pipeline-actions` @ `main` (`9a69bc2`)
**Reference consumer:** `muraliseelam/sfdx-pipeline-example` (callers on `@main`)
**Author:** QA / adversarial testing pass
**Date:** 2026-10-27
**Status of live assets:** scratch orgs `sfp-int/qa/stage/prod` (read-only in this pass), consumer repo live.

> This report is for three execution engineers (WS1/WS2/WS3). Every scenario is
> written to be run without asking the author anything. Findings are evidence-based
> and marked **CONFIRMED** (reproduced offline or seen in live logs/data) or
> **SUSPECTED** (code reading only).

---

## 1. Summary

### Scope & approach

Full static read of all 11 reusable workflows, 15 composite actions, 16 scripts, the
setup kit, the three test suites, `scripts/validate.sh`, both example-consumer sets and
all three docs; **offline experiments** against local git repos + extracted `run:`
blocks with fake `sf`/`gh` shims; **read-only live probes** (`gh api GET`, branch
protection, environments, the `audit-log` branch, the consumer config on every branch).
`validate.sh` + all three suites were run locally and **pass** (0 failures) — so the
findings below are things the current checks do **not** catch.

The pipeline is genuinely well hardened on the paths the four previous runs attacked
(status forgery, config trust, self-approval, ordering, phantom deletion, merge
pinning). The new findings come from going **wider** (shell-injection surface, the
release delta, audit integrity, portability regexes) and **deeper** on the
assemble/release path.

### Counts

| Workstream | Scenarios | OFFLINE | LIVE | LIVE-HUMAN |
|---|---|---|---|---|
| WS1 — Entry, validation & deploy engine | 34 | 18 | 13 | 3 |
| WS2 — Promotion, state & ops runbooks | 33 | 12 | 20 | 1 |
| WS3 — Release, sync & platform | 33 | 14 | 9 | 10 |
| **Total** | **100** | **44** | **42** | **14** |

| Predicted result | Count |
|---|---|
| PASS (guards the behaviour, expected green) | 78 |
| FAIL → a BUG below | 14 |
| UNKNOWN (needs live org/data to decide) | 8 |

| Findings by severity | Count | Confirmed | Suspected |
|---|---|---|---|
| Critical | 1 | 1 | 0 |
| High | 2 | 1 | 1 |
| Medium | 5 | 4 | 1 |
| Low | 3 | 2 | 1 |
| **Total** | **11** | **8** | **3** |

### Top risks

1. **BUG-001 (critical):** a feature branch can run **arbitrary shell on the runner,
   with the environment's org secrets, during PR validation — before any review** —
   by naming an Apex class so its name contains `$(…)`. The test-class name is
   interpolated unquoted into the deploy action's `run:` block.
2. **BUG-003 (high):** the production validate/deploy delta is **two-dot**; if `main`
   advances after *assemble* (a hotfix/revert) and the release is not re-assembled,
   the release turns those main-only components into **destructive changes** and
   production deletes them. Preflight explicitly allows a `BEHIND` release PR.
3. **BUG-002 (medium→high):** a `hold`, and the explicit `stories` list, gate
   **commits, not content** — a held/unselected story's metadata still ships to
   production if a released story merged `int` (or that branch) into itself.

---

## 2. Findings

### BUG-001 — Shell command injection via Apex test-class name (also `untested`)
- **Severity:** CRITICAL · **Category:** supply-chain / injection · **Owner:** WS1
- **Status:** **CONFIRMED** (reproduced offline; `INJECTION_MARKER` file created)
- **Exposed by:** TS-016, TS-017, TS-018
- **Where:** `sf-pipeline/deploy/action.yml:251` `TESTS="${{ steps.tests.outputs.tests }}"`
  and `:333` `UNTESTED="${{ steps.tests.outputs.untested }}"` (both inside the
  `Deploy / validate / quick-deploy` `run:` block). The values come from
  `sf-pipeline/scripts/specified_tests.py`, which derives them from **Apex class file
  names found under `source_path`** (`index_existing_classes` walks `*.cls`;
  `tests=` / `untested=` are written verbatim to `$GITHUB_OUTPUT`).
- **Failure scenario:** a developer with write access (or any PR author whose PR
  validation runs with the entry environment's secrets) adds a class file
  `force-app/main/default/classes/Mk$(command)Test.cls` containing `@isTest`. The
  name matches `specified_tests.TEST_RE`, so it is emitted as `tests=Mk$(command)Test`.
  GitHub Actions substitutes that text into the composite-action `run:` block, so bash
  evaluates `TESTS="Mk$(command)Test"` and executes `command`. This runs in the
  `validate` job of **pr-validation.yml**, i.e. **before approval**, inside the entry
  GitHub Environment (so `SF_*` secrets and `GITHUB_TOKEN` are in the process
  environment). `untested` (line 333) is the same class of bug for a changed Apex class
  with no matching test.
- **Evidence:** offline harness `e3` — a class file named
  `Mk$(touch INJECTION_MARKER)Test.cls` produced `sgd member: <members>Mk$(touch
  INJECTION_MARKER)Test</members>`, `specified_tests.py` wrote
  `tests=Mk$(touch INJECTION_MARKER)Test`, and rendering the real
  `deploy/action.yml` `run:` block with that value + a fake `sf` created
  `INJECTION_MARKER` (`rendered step exit=0`, file present). See Appendix A3.
- **Why existing guards miss it:** `notify`/`jira` deliberately pass PR titles and
  branch names *through `env:`* precisely to avoid this (`jira/action.yml:48`,
  `notify/action.yml:43`), but the deploy action interpolates `tests`/`untested`
  straight into the script.
- **Proposed fix:** never interpolate step outputs into a `run:` script. Pass
  `steps.tests.outputs.tests`/`untested`/`test_level` (and for safety
  `inputs.destructive_test_names`, BUG-005) through `env:` and read `"$TESTS"` in bash.
  Additionally, `specified_tests.py` should reject any class name that is not
  `[A-Za-z0-9_]+` (Apex identifiers can only be those), turning a hostile filename
  into a hard error. Add a unit test (BUG-006).

### BUG-002 — `hold` / story selection gates commits, not content (release ride-along)
- **Severity:** MEDIUM (argue HIGH) · **Category:** release integrity · **Owner:** WS3
- **Status:** **CONFIRMED** offline (held story's file shipped on the release branch)
- **Exposed by:** TS-079, TS-080, TS-092
- **Where:** `.github/workflows/prod-release.yml` `assemble` job (`git merge --no-edit
  "$sha"` of each gathered commit) + the "nothing unaccounted for" guard
  (`git rev-list --no-merges HEAD ^origin/$PROD_BRANCH ^<each proven sha>`), and
  `sf-pipeline/release-gather/action.yml` (gathers by commit).
- **Failure scenario:** story A's feature branch resolves a conflict the documented way
  is *not* followed — the developer runs `git merge origin/int` into their feature
  branch. A's merge commit `A2` now contains every squash already on `int`, including a
  story B that is on `hold` or simply not selected. When A is gathered and `assemble`
  merges `A2`, B's content lands on the release branch. The guard
  `rev-list … ^A2` excludes B's commits because they are **ancestors of the proven
  sha A2**, so assemble does not complain, and B reaches production.
- **Evidence:** offline harness `e2` — only `feature/PROJ-302-a` was gathered, yet the
  resulting `release/2026.300` contained `storyB.txt` (B merged to `int`, A merged
  `int`). `assemble exit=0`, "Release will include: feature/PROJ-302-a". See Appendix A2.
- **Nuance:** because the stage deltas are three-dot vs `main`, B's content was also
  deployed to int/qa/stage as a passenger of A — so this is primarily a **scope/`hold`
  bypass** (an operator's decision to exclude B is silently overridden), not always a
  clean environment-skip. With `delta_baseline: target` it can additionally become a
  skip (a stage that already had part of A's history validates a smaller delta).
- **Proposed fix:** after building the release branch, assemble should verify the
  branch **content** equals `main` + the union of each proven story's *own* three-dot
  delta (or forbid gathered commits that are merge commits pulling in `int`/another env
  branch). At minimum, warn when a gathered commit's ancestry includes an env branch
  tip, and when a `hold` story's content is present in the assembled tree.

### BUG-003 — Production release delta is two-dot; a hotfix on `main` after assemble is deleted
- **Severity:** HIGH · **Category:** delta / data loss · **Owner:** WS3
- **Status:** delta mechanism **CONFIRMED** offline; production deletion **SUSPECTED**
- **Exposed by:** TS-081, TS-082, TS-098
- **Where:** `.github/workflows/prod-release.yml` `validate` and `deploy` jobs call
  `sf-pipeline/deploy` **without** `delta_merge_base`, so `delta.sh` runs a two-dot
  `origin/<baseline>..HEAD`. `sf-pipeline/release-preflight/action.yml:108` explicitly
  accepts `BEHIND` as mergeable.
- **Failure scenario:** `assemble` cuts `release/X` from `main`. Then a change lands on
  `main` (an ops **Rollback** revert PR into the baseline, or any hotfix) that adds or
  keeps a component the release branch does not have. The release is run **without
  re-assembling**. At deploy time `delta.sh` diffs `origin/main..release/X` two-dot; the
  component present only on `main` becomes a **deletion** in
  `destructiveChanges.xml`. `prune_destructive.py` only drops deletions the **org does
  not have** — production *does* have the hotfix — so the deletion is kept and
  production loses it. Preflight does not force re-assembly (`BEHIND` is allowed).
- **Evidence:** offline harness `e1` — with `Hotfix__c` added to `main` after the
  release branch was cut, `DELTA_MERGE_BASE=false` (the release path) produced
  `destructive: <members>Account.Hotfix__c</members>`, while the three-dot mode used
  everywhere else produced `destructive: (none)`. See Appendix A1.
- **Proposed fix:** compute the production delta three-dot as well (pass
  `delta_merge_base: "true"` in both prod-release deploy calls — the merge-base of
  `main` and a release cut from `main` is `main`'s state at assemble, which is exactly
  what should ship), **or** make preflight refuse a `BEHIND` release PR and require
  re-assembly, **or** have assemble record the baseline SHA and diff against that.

### BUG-004 — Durable audit log records the ephemeral merge SHA, not the deployed commit
- **Severity:** MEDIUM · **Category:** audit integrity / docs-vs-behaviour · **Owner:** WS1
- **Status:** **CONFIRMED** live (consumer `audit-log` branch)
- **Exposed by:** TS-030, TS-031
- **Where:** `sf-pipeline/deploy/action.yml:531` and `:565` set `AUDIT_SHA: ${{ github.sha }}`.
  On a `pull_request*` event `github.sha` is the throwaway **merge ref** commit, which
  exists only for the run. `audit.py` writes it to `sha`/`short_sha`
  (`audit.py:62-63`). The real deployed commit is only in the `ref` field
  (`AUDIT_REF: ${{ inputs.ref }}` = the head SHA).
- **Evidence:** `git show origin/audit-log:audit/audit-log.jsonl` on the consumer — all
  `int` records have a `sha` that resolves to **MISSING** in the repo (e.g. `c9d03f89`,
  `91a20ae9`), while the correct deployed commit sits in `ref`
  (`d75fccf392d6`, `ba1d6f8f…`). All 58 records have `sha != ref`. Docs
  (Product_Documentation §16, User Guide Part 5) advertise `audit-log` as "a permanent,
  greppable history" of what deployed — but you cannot look a deployed commit up by its
  `sha`.
- **Proposed fix:** set `AUDIT_SHA` to the deployed commit (`inputs.ref`), and keep the
  run's merge SHA under a separate field if wanted. Add an `audit.py` field test.

### BUG-005 — Config values (`destructive_test_names`) interpolated unquoted into the deploy run block
- **Severity:** MEDIUM · **Category:** injection (config-sourced) · **Owner:** WS1 (fix), WS3 (config/doc)
- **Status:** **CONFIRMED** (same mechanism as BUG-001; value is admin/config-controlled)
- **Exposed by:** TS-019, TS-083
- **Where:** `sf-pipeline/deploy/action.yml:255` `CANARY_TESTS="${{ inputs.destructive_test_names }}"`
  (from `production.destructive_tests`). Also `:249-253` interpolate `inputs.test_level`,
  `steps.tests.outputs.test_level`, `steps.scope.outputs.*` — those are
  pipeline-controlled, but the pattern is fragile.
- **Failure scenario:** a `production.destructive_tests: "A$(cmd)"` in a consumer config
  injects on every production validate/deploy. Lower reach than BUG-001 (needs config
  write / is read from the trusted branch), but it is the one remaining config value
  that reaches a shell unquoted.
- **Proposed fix:** fold into the BUG-001 fix — pass all of these via `env:` and quote.

### BUG-006 — `specified_tests.py` has no test / validate coverage
- **Severity:** MEDIUM · **Category:** test gap · **Owner:** WS1
- **Status:** SUSPECTED (coverage gap is CONFIRMED; it is *why* BUG-001 shipped)
- **Exposed by:** TS-016, TS-032
- **Where:** `tests/` and `scripts/validate.sh` never import or run
  `specified_tests.py` (`grep` returns nothing). `test_deploy_engine.py` covers
  `prune`, `deploy_report`, `coverage-gate`, `manifest_to_md` only.
- **Proposed fix:** add `test_specified_tests.py` covering: test-class name matching,
  `@isTest` detection, no-match → `untested`, and **hostile names** (reject `$()`, back
  ticks, spaces). Wire it into `validate.sh` section 7b and `ci.yml`.

### BUG-007 — `node_version` validation regex allows path traversal characters
- **Severity:** LOW · **Category:** input validation consistency · **Owner:** WS1
- **Status:** CONFIRMED (regex behaviour)
- **Exposed by:** TS-020
- **Where:** `sf-pipeline/scripts/plan.py:48`
  `"node_version": re.compile(r"^[A-Za-z0-9.*/_-]+$")`. Unlike `source_path`, it has no
  `..` guard, so `../../etc` matches (verified). `node_version` only flows into
  `actions/setup-node`’s `node-version`, so impact today is limited to a confusing
  setup failure, but the asymmetry with `source_path` (which *does* block `..`) is a
  latent hazard if `node_version` is ever used in a path.
- **Proposed fix:** tighten to `^[0-9]+(\.[0-9]+){0,2}$|^(lts/\*|\*|latest)$` or at least
  add the same `(?!.*(^|/)\.\.(/|$))` guard.

### BUG-008 — `sync-branches` restore `trap` can run twice and mask a failed restore
- **Severity:** LOW · **Category:** ops robustness · **Owner:** WS3
- **Status:** SUSPECTED (code reading)
- **Exposed by:** TS-090
- **Where:** `sf-pipeline/sync-branches/action.yml:146-153` — `trap restore EXIT` is set,
  then `restore` is called explicitly, then `trap - EXIT`. If the explicit `restore`
  path `return`s early after setting `RESTORE_FAILED` (protection PUT failed), the loop
  continues to the next branch; the per-branch `trap`/`restore` bookkeeping around a
  `continue` on the conflict path (`:112`) leaves the `trap` from a *previous* iteration
  potentially still armed for the function-scope variables `SNAP`/`b`, which have been
  reassigned. Worst case: a restore is attempted against the wrong branch's snapshot, or
  a real restore failure is reported against a later branch. Needs a live multi-branch
  run with one protected branch whose restore is rejected to confirm.
- **Proposed fix:** scope the snapshot/trap per iteration in a subshell, or clear the
  trap at the top of every loop iteration and pass the branch explicitly to `restore`.

---

## 3. Scenario catalogue (TS-001 … TS-100)

Conventions. Story keys stay inside each workstream's range (WS1 `PROJ-1xx`,
WS2 `PROJ-2xx`, WS3 `PROJ-3xx`; release versions `2026.3xx`). Each LIVE story touches a
**unique** metadata file (a field on Account/Contact/Opportunity with a unique API
name, or a uniquely named Apex class) — never the shared `CustomLabels` unless the
scenario is a deliberate conflict test. Stories that reach `deployed:stage` but are not
for a release get the **`hold`** label. Every scenario ends by closing its attack PRs
and clearing labels/branches it created. "entry" = `int`, "baseline" = `main`.

### WS1 — Entry, validation & deploy engine (TS-001 … TS-034)

**TS-001 · WS1 · PR validation · Field-only change validates & reports**
Persona: careless dev. Pre: clean `int`.
Steps: 1) `git checkout -b feature/PROJ-101-field origin/main`; add
`Account.PROJ101__c` field; push; `gh pr create --base int`. 2) wait for PR Validation.
Expected: delta lists 1 CustomField; coverage cell "Not run (no Apex)"; one sticky
comment (`<!-- sf-pipeline-report -->`); `pr-validation=success` by `github-actions[bot]`;
the phantom deployment is deleted (`validate-status`). Pass/fail: status success + exactly
one comment + no lingering deployment. Mode: LIVE (~6 min). Predict: PASS.

**TS-002 · WS1 · PR validation · Apex at 100% passes the coverage gate**
Pre: consumer `coverage_threshold: 85`.
Steps: add `PROJ102.cls` + `PROJ102Test.cls` (100%); PR→int; wait.
Expected: coverage gate runs, aggregate ≥85%, per-class table rendered; `pr-validation=success`.
Pass/fail: success + coverage cell green. Mode: LIVE (~10 min). Predict: PASS.

**TS-003 · WS1 · PR validation · Apex below threshold fails before deploy**
Steps: add `PROJ103.cls` with a test giving ~80% coverage; PR→int.
Expected: coverage gate fails; `pr-validation=failure`; report section 2 red; nothing
deployed. Pass/fail: failure status + no `deploy-int`. Mode: LIVE (~10 min). Predict: PASS.

**TS-004 · WS1 · PR validation · Coverage exactly at threshold (85.00%)**
Steps: craft `PROJ104.cls`+test giving exactly 85.00%.
Expected: `coverage-gate.py` uses `pct + 1e-9 < threshold`, so 85.00 **passes**.
Pass/fail: success. Mode: OFFLINE (unit-feed `coverage-gate.py` with `numLocations`
summing to 85.00%) + LIVE confirm. Predict: PASS (boundary guarded).

**TS-005 · WS1 · PR validation · Changed Apex with no matching test fails fast**
Steps: add `PROJ105.cls` (no `PROJ105Test`); PR→int.
Expected: deploy action resolves no test, does **not** fall back to RunLocalTests, errors
with "No Apex test could be resolved for: PROJ105"; `pr-validation=failure`.
Pass/fail: failure + that message in the report. Mode: LIVE (~8 min). Predict: PASS.

**TS-006 · WS1 · PR validation · Config-only change skips the coverage gate**
Steps: PR changing only `force-app` layout/field; PR→int.
Expected: coverage gate skipped (no `*.cls`); validation still runs a check-only
`deploy start --dry-run`; broken XML would fail here. Pass/fail: gate skipped + dry-run ran.
Mode: LIVE (~6 min). Predict: PASS.

**TS-007 · WS1 · PR validation · `delta_baseline` correctness (prod vs target)**
Steps: offline — build a feature off `main`; set consumer `delta_baseline: prod` then
`target`; run `changes` action's `delta.sh` both ways against a drifted `int`.
Expected: `prod` diffs vs `main`; `target` diffs three-dot vs `int` (no spurious
deletions of other stories). Pass/fail: target mode shows only this branch's components.
Mode: OFFLINE (harness). Predict: PASS (three-dot guarded, `delta.sh:56-67`).

**TS-008 · WS1 · delta · merge-base uncomputable fails the run (no silent "no changes")**
Steps: OFFLINE — run `delta.sh` with `DELTA_MERGE_BASE=true` on a **shallow** clone where
merge-base is unavailable.
Expected: `delta.sh` exits non-zero with the "Refusing a two-dot diff" error, not
has_changes=false. Pass/fail: non-zero exit. Mode: OFFLINE. Predict: PASS (`delta.sh:64`).

**TS-009 · WS1 · delta · sgd writes no package.xml fails the run**
Steps: OFFLINE — fake `sf` shim that writes nothing; run `delta.sh`.
Expected: "wrote no package.xml. Refusing to treat that as 'no changes'" and exit 1.
Pass/fail: exit 1. Mode: OFFLINE. Predict: PASS (`delta.sh:84-87`).

**TS-010 · WS1 · deletions · deletion of a component the org lacks is skipped**
Steps: LIVE — PR that deletes a field the `int` org never had; approve; deploy.
Expected: `prune_destructive.py` drops it, report "Deletions skipped — not in int", deploy
succeeds and PR merges. Pass/fail: deploy success + skipped-list shown. Mode: LIVE (~8 min).
Predict: PASS (re-confirms §23 #63).

**TS-011 · WS1 · deletions · folder-based type deletion is always kept**
Steps: OFFLINE — `prune_destructive.py` with a `Report`/`Dashboard` deletion and a stub
lister.
Expected: never pruned (type in `UNLISTED`); deletion kept. Pass/fail: member stays in
destructiveChanges. Mode: OFFLINE (extend `test_deploy_engine.test_prune`). Predict: PASS.

**TS-012 · WS1 · deletions · case/URL-encoding difference keeps the deletion**
Steps: OFFLINE — org lists `My_Report`, delta deletes `My%5FReport` / `my_report`.
Expected: `norm()` compares decoded+casefold; since the org *has* it, it is pruned only
on a true match; a name the org lacks is kept. Pass/fail: matches §13 rules.
Mode: OFFLINE. Predict: PASS.

**TS-013 · WS1 · deploy · "nothing left" after pruning succeeds without deploying**
Steps: OFFLINE/LIVE — a destructive-only delta where every deletion is absent in the org.
Expected: deploy action mode `nothing-left`, outcome success, PR merges. Pass/fail:
success + "nothing-left". Mode: OFFLINE (render deploy run block). Predict: PASS.

**TS-014 · WS1 · deploy · gated mode: below-threshold Apex never reaches the org**
Steps: LIVE — approve a PR whose Apex is between Salesforce 75% and the configured 85%.
Expected: deploy runs `gated` (validate → coverage gate fails → **no** quick-deploy);
`deploy-int` not set; PR not merged; org unchanged. Pass/fail: no `deploy-int` + org has
no new class. Mode: LIVE (~12 min, scratch org read-back by `sf data`/`sf org list
metadata`). Predict: PASS.

**TS-015 · WS1 · deploy · gated two-job race (quick-deploy refused)**
Steps: LIVE — start two gated deploys into the same org close together (two PRs with Apex).
Expected: if Salesforce refuses the second quick-deploy, the run fails explicitly
("Salesforce refused the quick deploy … validate afresh"), nothing half-deployed.
Pass/fail: explicit failure, no partial. Mode: LIVE (~15 min). Predict: UNKNOWN (timing).

**TS-016 · WS1 · injection · Apex class name `$(…)` runs on the runner (BUG-001)**
Persona: malicious dev. Pre: write access.
Steps: 1) `git checkout -b feature/PROJ-106-inject origin/main`. 2) add
`force-app/main/default/classes/Mk$(id>/tmp/poc)Test.cls` with `@isTest private class
MkTest{@isTest static void t(){}}` and a matching `.cls-meta.xml`. 3) PR→int; let **PR
Validation** run (no approval needed). 4) inspect the `validate` job log / a benign marker.
Expected (correct behaviour): the name is rejected or passed through `env:` quoted, and
no command runs. Pass/fail: **FAIL** if the injected command executes (benign marker
appears) — the pipeline must treat the class name as data.
Mode: LIVE (~6 min; use a harmless marker only). Predict: **FAIL → BUG-001**
(reproduced offline — see Appendix A3; runs pre-approval with env secrets).

**TS-017 · WS1 · injection · `untested=` path (BUG-001 variant)**
Steps: add `Mk$(cmd)` (no test, not matching the test pattern) so it flows into
`untested=` and into the `MSG`/`::error::` line (`deploy/action.yml:333`).
Expected: no shell evaluation. Pass/fail: FAIL if the command runs.
Mode: OFFLINE (render the run block). Predict: **FAIL → BUG-001**.

**TS-018 · WS1 · injection · Fix verification — outputs via `env:` are inert**
Steps: OFFLINE — apply the proposed fix (pass `tests`/`untested` via `env:`, read
`"$TESTS"`); re-render with a `$()` value.
Expected: `TESTS` contains the literal string, no command runs; a non-identifier name is
rejected by `specified_tests.py`. Pass/fail: no marker + hard error on hostile name.
Mode: OFFLINE. Predict: PASS after fix (regression guard).

**TS-019 · WS1 · injection · `production.destructive_tests` with `$(…)` (BUG-005)**
Steps: OFFLINE — set `destructive_tests: "A$(touch poc)"`; render the deploy run block
for a prod destructive case.
Expected: value treated as literal test name. Pass/fail: FAIL if command runs.
Mode: OFFLINE. Predict: **FAIL → BUG-005**.

**TS-020 · WS1 · config · `node_version` traversal is rejected (BUG-007)**
Steps: OFFLINE — `plan.py outputs` with `node_version: "../../etc"`.
Expected: rejected like `source_path`. Pass/fail: FAIL if accepted.
Mode: OFFLINE. Predict: **FAIL → BUG-007** (regex accepts it; confirmed).

**TS-021 · WS1 · config trust · PR relaxing its own coverage gate is ignored**
Steps: LIVE — PR that also sets `apex_coverage: false` + `coverage_threshold: 50` in its
copy of `pipeline.config.yml`, plus low-coverage Apex; PR→int.
Expected: gates read from the protected base (`int`): true/85; gate still fails.
Pass/fail: failure at 85. Mode: LIVE (~10 min). Predict: PASS (re-confirms §23 #62).

**TS-022 · WS1 · config trust · PR on an unprotected base falls back to default branch**
Steps: LIVE — target a PR at an **unprotected** helper branch carrying a relaxed config.
Expected: `plan` detects "not protected" and reads the default branch's config
(`plan/action.yml:99-105`). Pass/fail: notice printed + default-branch gates used.
Mode: LIVE (~5 min). Predict: PASS.

**TS-023 · WS1 · config · `source_path` with shell metacharacters rejected**
Steps: OFFLINE — `plan.py outputs` with `source_path: "force-app; echo x"`.
Expected: rejected by `SAFE_VALUE`. Pass/fail: non-zero exit. Mode: OFFLINE. Predict: PASS.

**TS-024 · WS1 · config · single-environment chain (entry == last) resolves**
Steps: OFFLINE — config with one env; run `plan.py outputs/resolve/prdeploy`.
Expected: `entry_branch`, `last_env`, `release_label=deployed:<it>`, `r_require_envs=""`.
Pass/fail: matches `test_config_matrix`. Mode: OFFLINE. Predict: PASS.

**TS-025 · WS1 · config · `name != branch` resolve-by-name returns inert**
Steps: OFFLINE — env `{name: qa, branch: gh-actions-qa}`; `plan.py resolve qa`.
Expected: empty/`r_is_stage=false` (resolve is keyed by branch). Pass/fail: inert, no error.
Mode: OFFLINE. Predict: PASS (documented §6; covered by matrix).

**TS-026 · WS1 · bypass · admin bypass label honoured; non-admin ignored**
Steps: LIVE — (a) admin adds `bypass-quality-gates` to a low-coverage PR → approve →
deploys with NoTestRun; (b) a writer (non-admin) adds it → ignored, gate still runs.
Expected: `bypass` action re-verifies the labeller. Pass/fail: (a) deploys, (b) gated.
Mode: LIVE (~12 min, needs a non-admin collaborator or a PAT with write-not-admin).
Predict: PASS.

**TS-027 · WS1 · bypass · push after bypass drops it (stale)**
Steps: LIVE — admin adds bypass; push a new commit (synchronize).
Expected: pr-validation `Drop a bypass granted before this push` removes the label +
comments. Pass/fail: label gone + comment. Mode: LIVE (~6 min). Predict: PASS.

**TS-028 · WS1 · approve-guard · `approved` before green is stripped**
Steps: LIVE — add `approved` before validation finishes.
Expected: approve-guard removes it with "Approval held" comment; re-adding after green
sticks. Pass/fail: removed then accepted. Mode: LIVE (~6 min). Predict: PASS (re-confirms).

**TS-029 · WS1 · approval · self-approval honoured only with `allow_self_approval`**
Steps: LIVE — consumer has `allow_self_approval: true` on `int`. Author adds own
`approved`. Then test a branch whose base config has it `false` (a helper protected
branch) and confirm removal.
Expected: true → deploys; false → label removed with the explain comment. Pass/fail: both
behaviours. Mode: LIVE (~10 min). Predict: PASS.

**TS-030 · WS1 · audit · audit-log commit is unresolvable (BUG-004)**
Steps: LIVE-read — `git show origin/audit-log:audit/audit-log.jsonl`; for the newest
entries, `git cat-file -t <.sha>`.
Expected (correct): `.sha` resolves to the deployed commit. Pass/fail: FAIL if `.sha` is
MISSING. Mode: LIVE (read-only, ~2 min). Predict: **FAIL → BUG-004** (confirmed: all
`int` records' `sha` MISSING; deployed commit only in `.ref`).

**TS-031 · WS1 · audit · fix verification — `.sha` equals the deployed commit**
Steps: OFFLINE — set `AUDIT_SHA` to `inputs.ref`; run `audit.py` with a known head SHA.
Expected: `.sha == ref == deployed commit`. Pass/fail: equal. Mode: OFFLINE. Predict:
PASS after fix.

**TS-032 · WS1 · tests · `specified_tests.py` has a unit suite (BUG-006)**
Steps: OFFLINE — run the repo's test suite; grep for a `specified_tests` test.
Expected: a suite exists and is wired into `validate.sh`/`ci.yml`. Pass/fail: FAIL if none.
Mode: OFFLINE. Predict: **FAIL → BUG-006** (none today).

**TS-033 · WS1 · deploy · Apex deletion to prod uses canary / RunLocalTests, never NoTestRun**
Steps: OFFLINE — render the deploy run block for `ALIAS=prod`, `apex_destructive=true`,
empty vs set `destructive_tests`.
Expected: empty → `RunLocalTests`; set → `RunSpecifiedTests` with the canary; never
`NoTestRun`. Pass/fail: matches §11. Mode: OFFLINE. Predict: PASS.

**TS-034 · WS1 · deploy · profiles incremental self-diffs against `from_ref`**
Steps: OFFLINE — `profiles.py --mode incremental` on a profile where the target branch
updated an unrelated entry; baseline = `from_ref` (merge-base).
Expected: only entries this branch added/changed are kept; the other branch's entry is
not deployed back. Pass/fail: unrelated entry absent. Mode: OFFLINE (harness). Predict: PASS.

### WS2 — Promotion, state & ops runbooks (TS-035 … TS-067)

**TS-035 · WS2 · entry deploy · happy path: approve → deploy → merge → deployed:int**
Steps: LIVE — `feature/PROJ-201-base` (unique field), PR→int, approve.
Expected: ctx→plan→changes→bypass→guards→deploy→merge; `deploy-int=success` by bot;
squash-merge at the deployed SHA; `deployed:int` stamped; `approved` cleared.
Pass/fail: all of the above. Mode: LIVE (~10 min). Predict: PASS. (`hold` the PR if not
for a release.)

**TS-036 · WS2 · entry deploy · commit pushed after approval is refused pre-deploy**
Steps: LIVE — approve, then push a new commit before the deploy job reads the PR.
Expected: `Refuse if the PR moved` fires, `approved` removed, comment "a new commit … was
pushed", nothing deployed. Pass/fail: refusal + no deploy. Mode: LIVE (~8 min, racey).
Predict: PASS.

**TS-037 · WS2 · entry deploy · commit pushed DURING deploy → deployed-not-merged**
Steps: LIVE — push mid-deploy (as in §23 #61).
Expected: org gets the approved commit; merge refused (pinned to SHA);
no `deployed:int`; "the org has `<sha>`, the branch does not" comment. Pass/fail: that
exact state. Mode: LIVE (~10 min, timing). Predict: PASS.

**TS-038 · WS2 · entry deploy · merge-conflict PR blocked before the org**
Steps: LIVE — open a PR that conflicts with `int`; approve.
Expected: guard step 1 reports the conflict; no deploy. Pass/fail: conflict comment + no
`deploy-int`. Mode: LIVE (~6 min). Predict: PASS.

**TS-039 · WS2 · guard · mergeable=UNKNOWN after retries is a refusal**
Steps: OFFLINE — render the guard step with a `gh` shim returning UNKNOWN 8×.
Expected: refusal, exit 1. Pass/fail: exit 1 + UNKNOWN message. Mode: OFFLINE. Predict: PASS.

**TS-040 · WS2 · guard · ordering trusts only bot `deploy-<env>` for THIS commit**
Steps: OFFLINE — render the ordering-guard step with statuses posted by a non-bot creator.
Expected: ignored; guard blocks. Pass/fail: block. Mode: OFFLINE. Predict: PASS.

**TS-041 · WS2 · promotion · deploy:qa happy path (ordering passes)**
Steps: LIVE — after TS-035 merges to int, add `deploy:qa` to the int PR.
Expected: prepare opens feature→qa PR at the deployed SHA; ordering guard passes
(`int` done); deploy to qa; merge; `deployed:qa` on both PRs; `deploy:qa` cleared.
Pass/fail: all. Mode: LIVE (~10 min). Predict: PASS. Then `hold` the story.

**TS-042 · WS2 · promotion · deploy:stage requires int AND qa**
Steps: LIVE — add `deploy:stage` before qa is done.
Expected: refused — "must be deployed and merged in every earlier stage first
(int, qa)". Pass/fail: refusal lists qa missing. Mode: LIVE (~5 min). Predict: PASS.

**TS-043 · WS2 · promotion · `deploy:int` on a merged PR is "not a promotion"**
Steps: LIVE — add `deploy:int` to the merged int PR.
Expected: refused ("`deploy:int` is not a promotion"), label removed. Pass/fail: that
comment. Mode: LIVE (~4 min). Predict: PASS (re-confirms §23).

**TS-044 · WS2 · promotion · `deploy:prod` / unknown env rejected with valid list**
Steps: LIVE — add `deploy:prod` and `deploy:nope`.
Expected: `plan.py prdeploy` fails with "not a configured environment. Valid: …";
report_failure comments + removes the label. Pass/fail: both removed with the list.
Mode: LIVE (~5 min). Predict: PASS.

**TS-045 · WS2 · promotion · `deploy: qa` (with a space) rejected**
Steps: LIVE — add a label literally `deploy: qa`.
Expected: `sf-promote-label` envn step rejects "no spaces". Pass/fail: rejection.
Mode: LIVE (~4 min). Predict: PASS (`sf-promote-label.yml:106`).

**TS-046 · WS2 · promotion · feature tip moved past merged head → "has moved on"**
Steps: LIVE — after int merge, push a commit to the feature branch, then `deploy:qa`.
Expected: prepare `moved_on` refusal; no env PR left open. Pass/fail: refusal + no stray PR.
Mode: LIVE (~6 min). Predict: PASS (re-confirms §23 #58).

**TS-047 · WS2 · promotion · env-PR conflict surfaces on the control PR, no deploy**
Steps: LIVE — engineer a feature→qa conflict (qa has a conflicting change); `deploy:qa`.
Expected: prepare detects CONFLICTING; comments on control PR; closes the PR it opened;
removes `deploy:qa`. Pass/fail: conflict comment + env PR closed. Mode: LIVE (~8 min).
Predict: PASS.

**TS-048 · WS2 · promotion · idempotent re-add of deploy:qa after deployed:qa is a no-op**
Steps: LIVE — re-add `deploy:qa` once `deployed:qa` + bot `deploy-qa` exist.
Expected: "nothing to do" no-op; label cleared. Pass/fail: no-op comment, no second deploy.
Mode: LIVE (~4 min). Predict: PASS.

**TS-049 · WS2 · promotion · hand-added `deployed:qa` (no bot status) is NOT trusted**
Steps: LIVE — manually add `deployed:qa` to the control PR with no `deploy-qa` status; add
`deploy:qa`.
Expected: prepare does not treat it as done (promotes); ordering guard still gates on the
real earlier statuses. Pass/fail: promotion proceeds / correctly blocks on real statuses.
Mode: LIVE (~6 min). Predict: PASS.

**TS-050 · WS2 · promotion · forged bot-looking `deploy-qa` status ignored**
Steps: LIVE — POST a `deploy-qa=success` status with a personal token on the commit; add
`deploy:stage`.
Expected: only `github-actions[bot]` statuses count; stage still refused (`deploy-qa`
missing from bot). Pass/fail: refusal. Mode: LIVE (~5 min). Predict: PASS (re-confirms §23).

**TS-051 · WS2 · promotion · PIPELINE_PAT expired → "could not start", label re-addable**
Steps: OFFLINE — render the prepare `gh pr create` failure branch with a "Bad credentials"
stderr.
Expected: refuse with the PAT-rotation hint; `deploy:qa` removed so it can be re-added;
no stray PR. Pass/fail: that comment + label removed. Mode: OFFLINE. Predict: PASS.

**TS-052 · WS2 · promotion · "No commits between" is the one benign create failure**
Steps: OFFLINE — render prepare with a `gh pr create` stderr "No commits between".
Expected: treated as no-op (`noop`), not a failure. Pass/fail: no-op path. Mode: OFFLINE.
Predict: PASS.

**TS-053 · WS2 · promotion · deploy fails in gated mode → org and branch still agree**
Steps: LIVE — a qa promotion whose Apex fails the coverage gate.
Expected: `Record why` note "coverage gate failed on the validation … org and branch still
agree"; `deploy:qa` removed; env PR (if created) closed. Pass/fail: that note.
Mode: LIVE (~10 min). Predict: PASS. (qa is `NoTestRun` in the live consumer, so use a
config variant or a stage with a test level — see TS-054.)

**TS-054 · WS2 · promotion · NoTestRun stage skips the coverage gate**
Steps: LIVE — promote Apex to qa (consumer qa is `on_demand`/`NoTestRun`).
Expected: `coverage_gate` passed as `false` when `test_level==NoTestRun`
(`sf-promote-label.yml:432`); deploy runs plain; no gate. Pass/fail: no coverage gate step.
Mode: LIVE (~8 min). Predict: PASS.

**TS-055 · WS2 · promotion · control PR not merged → refused**
Steps: LIVE — add `deploy:qa` to an OPEN (unmerged) int PR.
Expected: refused "still open — not deployed and merged into int". Pass/fail: that comment.
Mode: LIVE (~4 min). Predict: PASS.

**TS-056 · WS2 · promotion · two different PRs promoting into one org concurrently**
Steps: LIVE — fire `deploy:qa` on two control PRs at once.
Expected: separate concurrency groups (per feature branch); Salesforce queues the org
deploys; both succeed in order; neither cancels the other. Pass/fail: both deployed+merged.
Mode: LIVE (~14 min). Predict: UNKNOWN (org queueing timing).

**TS-057 · WS2 · promotion · deploy:qa and deploy:stage added together**
Steps: LIVE — add both labels at once on a merged, int-only PR.
Expected: qa promotes; stage refuses until `deployed:qa`; re-adding `deploy:stage` after
qa succeeds (separate concurrency groups, §22). Pass/fail: ordering enforced.
Mode: LIVE (~12 min). Predict: PASS.

**TS-058 · WS2 · promotion · label re-added mid-run queues then no-ops**
Steps: LIVE — re-add `deploy:qa` while its run is in flight.
Expected: second run queues on `promote-label-deploy:qa-<branch>`, then no-ops (idempotent).
Pass/fail: one deploy, second no-op. Mode: LIVE (~8 min). Predict: PASS.

**TS-059 · WS2 · status forgery · hand-posted `pr-validation` ignored by approve-guard**
Steps: LIVE — POST `pr-validation=success` with a personal token; add `approved` before
real validation.
Expected: approve-guard counts only the bot status; removes `approved`. Pass/fail: removed.
Mode: LIVE (~5 min). Predict: PASS.

**TS-060 · WS2 · label state machine · `deployed:<env>` is a record not an instruction**
Steps: LIVE — manually add `deployed:stage` to an int-only story; confirm it does not
trigger anything and is not trusted by release-gather (cross-ref TS-079).
Expected: no promotion/release side effects. Pass/fail: inert. Mode: LIVE (~4 min).
Predict: PASS.

**TS-061 · WS2 · ops redeploy · dry_run default true is check-only**
Steps: LIVE — dispatch Redeploy with defaults for a feature into `int`.
Expected: check-only; no env branch moved; summary "check-only (dry run)". Pass/fail:
nothing merged. Mode: LIVE (~6 min). Predict: PASS.

**TS-062 · WS2 · ops redeploy · profiles_mode override applies**
Steps: LIVE — Redeploy with `profiles_mode=incremental` override.
Expected: deploy uses incremental; summary/audit show it. Pass/fail: incremental applied.
Mode: LIVE (~8 min). Predict: PASS.

**TS-063 · WS2 · ops redeploy · unknown environment fails with the valid list**
Steps: LIVE — Redeploy with `environment=nope`.
Expected: plan step errors "not a configured environment. Valid: int, qa, stage".
Pass/fail: that error. Mode: LIVE (~3 min). Predict: PASS.

**TS-064 · WS2 · ops recreate · existing branch needs confirm_reset=true**
Steps: LIVE — Recreate an existing `feature/PROJ-2xx` without `confirm_reset`.
Expected: refused "Re-run with confirm_reset=true". With true → force-reset to baseline.
Pass/fail: both. Mode: LIVE (~4 min). Predict: PASS.

**TS-065 · WS2 · ops recreate · weird name is normalised safely**
Steps: LIVE — Recreate with `name="PROJ 299 foo/../bar"`.
Expected: normalised to `feature/PROJ-299-foo/bar` (spaces→`-`, `..` stripped by the
`tr -cd` filter keeps `.`/`/`). Verify no path escape. Pass/fail: safe branch name.
Mode: LIVE (~3 min). Predict: UNKNOWN (the `tr -cd 'A-Za-z0-9._/-'` keeps `.` and `/`, so
`..`/nested paths survive — review whether `feature/a/../b` is acceptable).

**TS-066 · WS2 · ops rollback · unmerged PR refused; merged PR opens a revert PR**
Steps: LIVE — Rollback with (a) an open PR number → refused "not merged"; (b) a merged PR
→ revert branch + PR into the entry branch.
Expected: (a) refusal; (b) revert PR that then flows through validate→approve→deploy.
Pass/fail: both. Mode: LIVE (~8 min). Predict: PASS.

**TS-067 · WS2 · ops rollback · conflicting revert stops with manual instructions**
Steps: LIVE — Rollback a change that conflicts on the base.
Expected: "Revert … hit conflicts … Resolve manually" with the exact git commands; no
force deploy. Pass/fail: that message. Mode: LIVE (~6 min). Predict: PASS.

### WS3 — Release, sync & platform (TS-068 … TS-100)

**TS-068 · WS3 · assemble · gathers proven stories, skips already-shipped**
Steps: LIVE — with ≥1 story at `deployed:stage` proven across all stages, run
`assemble`.
Expected: `release-gather` collects it at its deployed commit, skips shipped ones, pushes
`release/<ISO week>`; prints a compare link (does **not** open the PR). Pass/fail: branch
pushed + summary table. Mode: LIVE (~6 min). Predict: PASS.

**TS-069 · WS3 · assemble · nothing to release is an error**
Steps: LIVE — run `assemble` with no unshipped proven story.
Expected: `release-gather` exits with "No merged PRs are labeled 'deployed:stage'…" (or
"No live release branches remain…"); run fails. Pass/fail: explicit error, no empty branch.
Mode: LIVE (~4 min). Predict: PASS.

**TS-070 · WS3 · assemble · explicit `stories` narrows but still requires proof**
Steps: LIVE — `assemble` with `stories=feature/PROJ-301-x` where x is int-only.
Expected: fails "not deployed to every stage by the pipeline". Pass/fail: that error.
Mode: LIVE (~5 min). Predict: PASS.

**TS-071 · WS3 · assemble · stale release branch with unaccounted commits is refused**
Steps: LIVE — pre-create `release/2026.301` with a hand commit; run `assemble` for it.
Expected: "already contains commits that no story proven deployed to every stage accounts
for …"; refuse. Pass/fail: refusal. Mode: LIVE (~5 min). Predict: PASS (re-confirms b5c75fe).

**TS-072 · WS3 · release-gather · hand-added `deployed:stage` on an int-only story ignored**
Steps: LIVE — hand-label an int-only PR `deployed:stage`; run `assemble`.
Expected: warning "a label is not proof"; story left out. Pass/fail: excluded.
Mode: LIVE (~5 min). Predict: PASS (re-confirms §23).

**TS-073 · WS3 · release-gather · forged bot-name `deploy-stage` status not trusted**
Steps: OFFLINE — feed `release-gather`'s status loop a `deploy-stage=success` whose
creator != `github-actions[bot]`.
Expected: not counted; `MISS` includes it with "the one set by @x is not trusted".
Pass/fail: not gathered. Mode: OFFLINE (harness extract). Predict: PASS.

**TS-074 · WS3 · release-gather · `hold` on ANY of a story's PRs holds it**
Steps: LIVE — put `hold` on a control PR whose story is otherwise proven; run `assemble`.
Expected: story excluded (held). Pass/fail: excluded. Mode: LIVE (~5 min). Predict: PASS.

**TS-075 · WS3 · release-gather · deleted story branch drops out, not fail**
Steps: OFFLINE — gather set includes a merged PR whose head branch no longer exists.
Expected: "Skipping … branch no longer exists" notice; others still release. Pass/fail:
survivors returned. Mode: OFFLINE. Predict: PASS.

**TS-076 · WS3 · release-gather · newest-commit-not-in-baseline is chosen; later tip ignored**
Steps: OFFLINE — a story with multiple candidate commits (rebased); baseline already has
the older one.
Expected: picks the newest proven commit not yet in baseline; warns about commits after
it. Pass/fail: correct pick. Mode: OFFLINE. Predict: PASS.

**TS-077 · WS3 · preflight · unauthorized release PR refused (no deploy)**
Steps: LIVE-HUMAN setup / LIVE run — open a release PR with no approval/label; run
`release`.
Expected: preflight "is not authorized"; nothing validated/deployed. Pass/fail: refusal
pre-validate. Mode: LIVE (~4 min). Predict: PASS.

**TS-078 · WS3 · preflight · stale Approve (branch changed) refused; label re-auth works**
Steps: LIVE — approve the release PR, push to the release branch, run `release`.
Expected: "approved, but not at its current commit"; adding `prod-approved` re-authorizes.
Pass/fail: both. Mode: LIVE (~6 min). Predict: PASS.

**TS-079 · WS3 · release integrity · held story rides in via an `int` merge (BUG-002)**
Persona: careless RM / dev. Pre: story A proven; story B on `hold`, merged to `int`.
Steps: 1) on `feature/PROJ-302-a`, resolve a conflict with `git merge origin/int` (pulls
B's squash). 2) promote A through int→qa→stage (proven). 3) `hold` B. 4) `assemble`.
Expected (correct): B's content must NOT be on the release branch; assemble should warn
that a gathered commit pulls in an env branch. Pass/fail: **FAIL** if B's file is on
`release/<v>`. Mode: LIVE (~20 min) / OFFLINE (reproduced). Predict: **FAIL → BUG-002**
(offline `e2`: `storyB.txt` shipped though only A gathered).

**TS-080 · WS3 · release integrity · assemble "nothing unaccounted" guard vs ride-along**
Steps: OFFLINE — run the assemble `Build release branch` step on the `e2` repo.
Expected: the guard should catch passenger content; today it passes because B's commits
are ancestors of A's proven merge sha. Pass/fail: FAIL (guard passes, B present).
Mode: OFFLINE. Predict: **FAIL → BUG-002**.

**TS-081 · WS3 · release delta · hotfix on main after assemble is deleted (BUG-003)**
Persona: GitHub/SF misbehaving + RM under pressure. Pre: `release/2026.3xx` assembled.
Steps: 1) land a change on `main` after assemble (e.g. via ops Rollback revert into
baseline, or a direct admin hotfix) that adds `Account.PROJ303Hotfix__c`. 2) authorize the
existing release PR. 3) run `release` **without re-assembling**.
Expected (correct): the hotfix must survive production. Pass/fail: **FAIL** if the prod
delta carries `destructiveChanges` deleting `PROJ303Hotfix__c`. Mode: LIVE-HUMAN (prod
reviewer gate) — **the whole pre-deploy delta is automatable: run to `validate` and read
the manifest/`destructiveChanges.xml` artifact, then CANCEL before the prod gate**; do not
deploy. Predict: **FAIL → BUG-003** (two-dot delta confirmed offline `e1`).

**TS-082 · WS3 · release delta · fix verification — three-dot prod delta drops the phantom deletion**
Steps: OFFLINE — rerun `e1` with `DELTA_MERGE_BASE=true` for the prod delta.
Expected: no destructive entry for the main-only component. Pass/fail: destructive empty.
Mode: OFFLINE. Predict: PASS after fix.

**TS-083 · WS3 · injection · `destructive_tests` config `$(…)` on prod deploy (BUG-005)**
Steps: OFFLINE — render the prod deploy run block with a hostile `destructive_tests`.
Expected: literal test name. Pass/fail: FAIL if command runs. Mode: OFFLINE.
Predict: **FAIL → BUG-005**.

**TS-084 · WS3 · validate · no-Apex release validated check-only, then full deploy**
Steps: LIVE-HUMAN — a field-only release; run to the prod gate.
Expected: validate uses `deploy start --dry-run` (no tests), `validated=true quick=false`;
chat/summary say "the deploy will be a full deploy". Pass/fail: those signals.
Mode: LIVE-HUMAN (automatable up to the gate; cancel). Predict: PASS.

**TS-085 · WS3 · validate · Apex release captures a quick-deploy id**
Steps: LIVE-HUMAN — an Apex release; run to the prod gate.
Expected: validate `RunSpecifiedTests` returns a `deploy_id`; `quick=true`; summary names
the job to quick-deploy. Pass/fail: id captured. Mode: LIVE-HUMAN (to the gate). Predict: PASS.

**TS-086 · WS3 · deploy · quick-deploy refused stops before merge/tag, explicit error**
Steps: LIVE-HUMAN — validate, then cause the quick deploy to be refused (e.g. let the
validation age or land another deploy); approve; deploy.
Expected: "Salesforce refused the quick deploy …"; no merge, no tag; summary gives the
safe retry. Pass/fail: that error + no tag. Mode: LIVE-HUMAN. Predict: UNKNOWN (hard to
force refusal on demand).

**TS-087 · WS3 · deploy · second `release` run on a `deployed:prod` PR refused**
Steps: LIVE — run `release` again after a release deployed (PR carries `deployed:prod`).
Expected: preflight refuses "carries deployed:prod". Pass/fail: refusal. Mode: LIVE (~4
min; use a PR already carrying the label). Predict: PASS.

**TS-088 · WS3 · finalize · re-run of a failed finalize skips an already-merged PR**
Steps: OFFLINE — render the finalize `publish` step with a `gh` shim reporting the PR
already MERGED.
Expected: "already merged", proceeds to tag/release idempotently. Pass/fail: no second
merge. Mode: OFFLINE. Predict: PASS.

**TS-089 · WS3 · finalize · release branch moved after preflight → merge refused at HEAD_SHA**
Steps: OFFLINE — finalize with the live PR head != preflight `HEAD_SHA`.
Expected: `--match-head-commit` refuses; explicit error; no tag. Pass/fail: refusal.
Mode: OFFLINE. Predict: PASS.

**TS-090 · WS3 · sync-branches · protection round-trip + restore trap (BUG-008)**
Steps: LIVE — after a release (or a dedicated run), confirm each env branch's protection
is restored (app-pinned `deploy-<env>`, reviews). Then OFFLINE-render the sync step with a
`gh` shim that fails one branch's protection PUT.
Expected: protection restored on all; a failed restore is reported against the **correct**
branch and the step fails. Pass/fail: no branch left unprotected; correct branch named.
Mode: LIVE (read protection) + OFFLINE (trap). Predict: UNKNOWN → possibly FAIL → BUG-008.

**TS-091 · WS3 · sync-branches · env branch with unreleased stories gets a merge, not a reset**
Steps: OFFLINE — `sync-branches` where an env branch has a commit not in baseline.
Expected: baseline is merged in (merge commit by sf-release-bot), not reset. Pass/fail:
merge, nothing discarded. Mode: OFFLINE (harness). Predict: PASS.

**TS-092 · WS3 · sync-branches · conflicting env branch is left as is with a warning**
Steps: OFFLINE — env branch conflicts with baseline.
Expected: `merge-tree` reports conflict → "left as is … Resolve with Sync Env Branches";
`--force-with-lease` never overwrites. Pass/fail: branch untouched + warning. Mode: OFFLINE.
Predict: PASS.

**TS-093 · WS3 · Sync Env Branches workflow · protected branch push falls back to a PR**
Steps: LIVE — dispatch Sync Env Branches without an admin PAT (workflow token) so the push
to a protected branch is rejected.
Expected: opens a sync PR cut from the production branch (with its commits so conflicts
show), or warns if Actions-PR creation is off; never fails the job hard. Pass/fail: PR or
warning. Mode: LIVE (~6 min). Predict: PASS.

**TS-094 · WS3 · project-init · dry run then apply is additive/idempotent**
Steps: LIVE — run Project Init apply=false then true; re-run true.
Expected: creates branches/labels/environments; re-run skips existing; never deletes.
Pass/fail: idempotent. Mode: LIVE (~5 min, on a throwaway repo or confirm no-op on the
consumer). Predict: PASS.

**TS-095 · WS3 · project-init · prod envs limited to the production branch only**
Steps: LIVE-read — `gh api repos/<consumer>/environments/prod(-validate)/deployment-branch-policies`.
Expected: custom policy = only `main`. Pass/fail: only `main`. Mode: LIVE (read-only, ~2
min). Predict: PASS (confirmed: both custom, `main` only).

**TS-096 · WS3 · env policy · release/* cannot use prod secrets**
Steps: LIVE — open a PR INTO a `release/**` branch (validation resolves `prod-validate`).
Expected: refused at the environment ("not allowed to deploy to prod-validate"); release is
validated by the train itself. Pass/fail: refusal. Mode: LIVE (~5 min). Predict: PASS.

**TS-097 · WS3 · branch protection · every pipeline branch is PR-only + app-pinned statuses**
Steps: LIVE-read — `setup-branch-protection.sh` DRY_RUN=true offline, plus `gh api` on the
consumer's `int`/`qa`/`stage`/`main` protection.
Expected: `int` requires `pr-validation`+`deploy-int` @15368, 0 reviews (solo);
`qa`/`stage` require `deploy-<env>`; `main` PR-only no status; no force-push/deletion.
Pass/fail: matches. Mode: OFFLINE + LIVE-read (~4 min). Predict: PASS (confirmed:
`int` checks `deploy-int@15368,pr-validation@15368`).

**TS-098 · WS3 · multi-config portability · renamed/slashed/many/one envs**
Steps: OFFLINE — run `plan.py` + `setup-branch-protection.sh` DRY_RUN + `init-project.sh`
dry against configs: (a) `mon→tue→wed` on `env/monday…` branches (names != branches,
slashes); (b) a 1-env chain; (c) a 6-env chain; (d) `release_label` override; (e) a
`gate: auto` entry.
Expected: branches/labels/statuses all derive from config; slashes handled; auto entry
gets 0 reviews + no pr-validation status. Pass/fail: all derive correctly. Mode: OFFLINE.
Predict: PASS (matrix + §23 renamed chain back this).

**TS-099 · WS3 · versioning · `@v1` tag rewrites all 51 internal refs; `cut-release.sh`**
Steps: OFFLINE — run `scripts/cut-release.sh vX` (no push) on a clone; run
`scripts/verify-tag.sh`; grep the tag's `deploy.yml` for `@main`.
Expected: all internal `uses:` rewritten to `@vX` (51 refs across 11 workflows +
approval), none left `@main`; verify-tag passes. Pass/fail: 0 `@main` on the tag.
Mode: OFFLINE. Predict: PASS (51 refs confirmed in-tree).

**TS-100 · WS3 · org bootstrap · ECA fallback + reusable kit checkout at the pinned ref**
Steps: OFFLINE/LIVE-read — inspect `org-bootstrap.yml` kit-checkout logic and
`sf-setup/` (connected app + External Client App). Verify `SELF` ref rewriting so `@v1`
checks out the v1 kit; verify `push-env-secrets.sh` never re-PUTs an existing env.
Expected: kit fetched from the central repo at the caller's ref; ECA used when Connected
Apps are refused; existing prod env not disturbed. Pass/fail: logic holds. Mode: OFFLINE
(code) — a real bootstrap needs an org admin login (out of scope here). Predict: PASS.

---

## 4. Workstream split & file ownership

Balanced at **34 / 33 / 33**. Each workstream owns the fixes for the BUGs tagged to it.

### WS1 — Entry, validation & deploy engine (TS-001…TS-034) — owns BUG-001, BUG-004, BUG-005(fix), BUG-006, BUG-007
Files: `.github/workflows/pr-validation.yml`, `deploy.yml`, `approve-guard.yml`;
`sf-pipeline/{plan,approval,bypass,guard,deploy,changes,coverage,report}`;
`sf-pipeline/scripts/{plan.py, delta.sh, prune_destructive.py, profiles.py,
specified_tests.py, deploy_report.py, manifest_to_md.py, audit.py, audit-append.sh,
coverage-gate.py}`; `tests/test_config_matrix.py`, `tests/test_deploy_engine.py`.
Priority: **BUG-001** (route `tests`/`untested`/`destructive_test_names` through `env:` +
validate class names in `specified_tests.py`; add `tests/test_specified_tests.py`),
**BUG-004** (`AUDIT_SHA` → deployed commit), BUG-007 (tighten `node_version`).

### WS2 — Promotion, state & ops runbooks (TS-035…TS-067) — no new BUG (review TS-065 name normalisation)
Files: `.github/workflows/sf-promote-label.yml`, `sf-ops-redeploy-feature.yml`,
`sf-ops-recreate-feature.yml`, `sf-ops-rollback.yml`;
`sf-pipeline/{merge-pr,status,jira,notify}` + their scripts.
Note: review `sf-ops-recreate-feature.yml` name normalisation (`tr -cd 'A-Za-z0-9._/-'`
keeps `.`/`/`, so `feature/a/../b` survives) — TS-065.

### WS3 — Release, sync & platform (TS-068…TS-100) — owns BUG-002, BUG-003, BUG-005(config/doc), BUG-008
Files: `.github/workflows/prod-release.yml`, `sync-env-branches.yml`, `project-init.yml`,
`org-bootstrap.yml`, `ci.yml`; `sf-pipeline/{release-gather,release-preflight,sync-branches}`;
`sf-pipeline/scripts/{setup-branch-protection.sh, init-project.sh}`; `sf-setup/**`;
`scripts/{validate.sh, cut-release.sh}`; `tests/test_branch_protection.py`; `examples/**`;
docs. Priority: **BUG-003** (three-dot prod delta or force re-assembly / refuse BEHIND),
**BUG-002** (verify release-branch *content*, not just commit ancestry).

---

## 4b. Execution results (2026-10-27)

All 100 scenarios were executed by three workstreams against the live consumer
and offline harnesses; every fix was pushed to `main` (CI green) and re-verified.
Per-scenario evidence (PRs, run ids, org queries, harness commands):
[WS1](results/WS1_Results.md) · [WS2](results/WS2_Results.md) · [WS3](results/WS3_Results.md).

| Workstream | PASS | FAIL→FIXED | BLOCKED-HUMAN | BLOCKED-CLASSIFIER | N/A | Total |
|---|---|---|---|---|---|---|
| WS1 Entry, validation & deploy engine | 26 | 6 | 1 | 1 | 0 | 34 |
| WS2 Promotion, state & ops runbooks | 30 | 2 | 0 | 0 | 1 | 33 |
| WS3 Release, sync & platform | 28 | 4 | 1 | 0 | 0 | 33 |
| **Total** | **84** | **12** | **2** | **1** | **1** | **100** |

- **Findings:** BUG-001…BUG-007 fixed and verified (BUG-001 live: a test class
  named `Mk$(echo …)Test.cls` is refused and never expanded). BUG-008 did not
  reproduce; a regression test now guards it. New findings NEW-WS1-01…04,
  NEW-WS2-01…06 and NEW-WS3-01…04 are fixed (NEW-WS3-01 — Redeploy's two-dot
  delta — in `035caf5`, verified live: run 40618331570 diffed from the merge-base
  and listed only the branch's own changes). NEW-WS1-05 (sandbox GitHub
  Environments have no branch policy, so PR code can read sandbox credentials) is
  a documented trade-off: restricting them would block PR validation.
- **Needs a human:** TS-014 (a `gate: auto` admin push to `env/monday` was denied
  by the agents' safety classifier — the script and expected output are in the WS1
  results), TS-026(b) (a non-admin collaborator), TS-086 and the production halves
  of TS-078/TS-081 (an approved production deploy — agents never approve the
  `prod` gate). No production deploy was approved during this run.

## 5. Appendix — reproductions

Tooling: Git Bash; base Python `C:\Users\mural\AppData\Local\Programs\Python\Python312\python.exe`
(PyYAML 6.0.3) exposed as `python3` via a shim on PATH (the venv's `python3` has a broken
namespace `yaml`); `MSYS2_ARG_CONV_EXCL='*'` for `gh api` paths; `PYTHONUTF8=1` for Python.
Scratch area: `…\scratchpad\qa\`.

### A1 — BUG-003 two-dot prod delta deletes a main-only component (harness `e1`)
```bash
# bare origin + working clone; main has Base__c; release/2026.300 adds Story__c;
# then main gains Hotfix__c AFTER the release branch was cut.
DELTA_FROM=origin/main DELTA_TO=HEAD DELTA_OUTPUT=.sgd2 DELTA_MERGE_BASE=false \
  bash sf-pipeline/scripts/delta.sh          # the prod-release path
# => package: <members>Account.Story__c</members>
# => destructiveChanges: <members>Account.Hotfix__c</members>   # deletes the hotfix!
DELTA_FROM=origin/main DELTA_TO=HEAD DELTA_OUTPUT=.sgd3 DELTA_MERGE_BASE=true \
  bash sf-pipeline/scripts/delta.sh          # three-dot (everywhere else)
# => package: <members>Account.Story__c</members>; destructiveChanges: (none)
```

### A2 — BUG-002 held story rides into the release (harness `e2`)
```bash
# feature/PROJ-301-b (B) merged to int (squash); feature/PROJ-302-a (A) runs
# `git merge origin/int` → A2 (a merge commit containing B's squash).
# Gather ONLY A; run the real assemble "Build release branch" run: block with SHAS=$A2.
STORIES="feature/PROJ-302-a" SHAS="$A2" PROD_BRANCH=main BR=release/2026.300 \
  GITHUB_OUTPUT=/dev/null GITHUB_STEP_SUMMARY=/dev/null bash assemble_build.sh
# assemble exit=0, "Release will include: feature/PROJ-302-a"
git ls-tree -r --name-only origin/release/2026.300 force-app
#   force-app/f/base.txt
#   force-app/f/storyA.txt
#   force-app/f/storyB.txt   <-- B shipped though it was never gathered / was on hold
```

### A3 — BUG-001 command injection via Apex class name (harness `e3`)
```bash
# A class file named with a command substitution and @isTest:
#   force-app/main/default/classes/Mk$(touch INJECTION_MARKER)Test.cls
bash sf-pipeline/scripts/delta.sh            # DELTA_MERGE_BASE=true
#   sgd member: <members>Mk$(touch INJECTION_MARKER)Test</members>
python3 sf-pipeline/scripts/specified_tests.py --dir .sgd --source-root force-app
#   tests=Mk$(touch INJECTION_MARKER)Test        (written to $GITHUB_OUTPUT verbatim)
# Render the REAL deploy/action.yml "Deploy / validate / quick-deploy" run: block with
# that value substituted for ${{ steps.tests.outputs.tests }} (a fake `sf` on PATH):
PATH="fakebin:$PATH" GITHUB_OUTPUT=/dev/null bash rendered.sh
ls -la INJECTION_MARKER      # -> file exists: the injected command ran
```
Render helper (`render_action_step.py`) does the same textual `${{ }}` substitution the
Actions runner does for composite `run:` blocks, confirming the runner would evaluate it.

### A4 — Live read-only probes (consumer `muraliseelam/sfdx-pipeline-example`)
```bash
gh api repos/<consumer>/branches/int/protection --jq '.required_status_checks.checks'
#   deploy-int@15368, pr-validation@15368   (app-pinned, confirms TS-097)
gh api repos/<consumer>/environments/prod/deployment-branch-policies
#   custom, only "main"                      (confirms TS-095)
git show origin/audit-log:audit/audit-log.jsonl | tail
#   int records: .sha (=github.sha, merge ref) is unresolvable; deployed commit only in .ref (BUG-004)
```

### A5 — Baseline: existing checks pass
`PYTHONUTF8=1 bash scripts/validate.sh` → **All checks passed** (14 sections);
`tests/test_config_matrix.py` 81 checks, `test_deploy_engine.py` 39 checks,
`test_branch_protection.py` all pass. The findings above are gaps these do **not** cover.

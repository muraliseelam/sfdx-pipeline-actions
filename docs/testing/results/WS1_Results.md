# WS1 results — Entry, validation & deploy engine (TS-001 … TS-034)

**Engineer:** WS1 · **Date:** 2026-10-27/28 (UTC) · **Consumer:** `muraliseelam/sfdx-pipeline-example`
(own clone, callers on `@main`) · **Stories:** PROJ-101 … PROJ-111 · **Org:** `int` (scratch org `sfp-int`)

Pipeline commits under test: `b439e8b` (BUG-001/004/005/006/007) was pushed **before** any
live scenario ran; `526f8e5` and `65fc385` followed (see *Fixes pushed*).

## Counts

| Result | Count |
|---|---|
| PASS | 26 |
| FAIL→FIXED | 6 (TS-016, 017, 019, 020, 030, 032) |
| BLOCKED-HUMAN | 1 |
| BLOCKED-CLASSIFIER | 1 |
| **Total** | **34** |

Offline suite: `tests/test_deploy_engine.py` 91 checks, `tests/test_config_matrix.py` 95 checks,
`PYTHONUTF8=1 bash scripts/validate.sh` → *All checks passed* after every fix (and after rebasing
onto WS3's `069095f`). Against the pre-fix tree (`8fd014f`) the same deploy-engine suite fails
16 checks — including the two where the injected `touch` **ran** — and the matrix fails 3
(`node_version` traversal).

## Scenarios

PR/run numbers are in `muraliseelam/sfdx-pipeline-example`.

| TS | title | mode | result | evidence | notes |
|---|---|---|---|---|---|
| TS-001 | Field-only change validates & reports | LIVE | PASS | PR #91 (PROJ-101); validation run 40610626965 | 1 CustomField in the delta; coverage cell "⚪ Not run (no Apex)"; exactly one `<!-- sf-pipeline-report -->` comment; `pr-validation=success` by `github-actions[bot]`; `validate-status` ran and `GET /deployments?sha=<head>` returns `[]` (phantom deleted). |
| TS-002 | Apex at 100% passes the coverage gate | LIVE | PASS | PR #93 (PROJ-102); validation 40610725926; deploy 40611423626 | Report: `🟢 100% (min 85%)`, per-class `PROJ102 100% (2/2)`. Approved → gated deploy → merged, `deployed:int`. |
| TS-003 | Apex below threshold fails before deploy | LIVE | PASS | PR #96 (PROJ-103); validation 40610818000 | `Aggregate Apex coverage: 80.00% (threshold 85%)` → `##[error]Apex code coverage 80.00% is below the required 85%`; report section 2 `🔴 80% (min 85%)`; `pr-validation=failure`; no `deploy-int` status. PR closed. |
| TS-004 | Coverage exactly at threshold (85.00%) | OFFLINE + LIVE | PASS | offline `test_coverage_gate` (17/20 → exit 0, 16/20 → exit 1); PR #94 (PROJ-104) validation 40610730730, deploy 40611940869 | Class calibrated to exactly 20 locations / 3 uncovered (check-only calibration in sfp-int, ids 0Afh1000000gPoPCAU). Report `🟢 85% (min 85%)`, `PROJ104 85% (17/20)`; the deploy's own gate logged `Aggregate Apex coverage: 85.00%` and passed. |
| TS-005 | Changed Apex with no matching test fails fast | LIVE | PASS | PR #97 (PROJ-105); validation 40610821789 | `##[error]No Apex test could be resolved for: PROJ105 …` in the log and the report; no RunLocalTests fallback. (`UNTESTED` now arrives through `env:`.) PR closed. |
| TS-006 | Config-only change skips the coverage gate | LIVE | PASS | PR #91; run 40610626965 | `Delta contains Apex (additive): false` → `No Apex in the delta — using NoTestRun` → `Check-only deploy to int (no Apex tests to run)` (`deploy start --dry-run`); `end-action id=deploy.coverage;outcome=skipped`. |
| TS-007 | `delta_baseline` correctness (prod vs target) | OFFLINE | PASS | `scratchpad/ws1/ts007.sh` — real `delta.sh` + sfdx-git-delta 6.45.1 on the live consumer history | `feature/PROJ-101-field`: prod (vs `main`) → `Account.PROJ101__c`; target three-dot (vs drifted `int`, merge-base `b88360b`) → `Account.PROJ101__c`, 0 deletions; the two-dot diff it guards against would delete 18 other stories' components. |
| TS-008 | merge-base uncomputable fails the run | OFFLINE | PASS | `test_delta` | Unrelated/shallow baseline → exit 1, "Refusing a two-dot diff", no `has_changes` written. |
| TS-009 | sgd writes no package.xml fails the run | OFFLINE | PASS | `test_delta` | Fake `sf sgd` writing nothing → exit 1, "wrote no …package.xml". |
| TS-010 | Deletion of a component the org lacks is skipped | LIVE | PASS | PR #107 (PROJ-108); validation 40612019568 | Deleting label `E2E_Hardened` (on `main`, absent from the `int` org): `##[warning]Skipped deleting CustomLabel 'E2E_Hardened': it is not in the 'int' org`, `Nothing left to deploy to int`, PR report "⏭️ Deletions skipped — not in `int` (1)", status success. **Not approved/merged on purpose:** the only `main`-only component the int org lacks lives in the shared `CustomLabels.labels-meta.xml`, and merging would delete that file from `int` under WS2/WS3. The real-deploy branch of the same step (`CHECK=false`, nothing-left) is covered offline (`test_deploy_action`). PR closed. |
| TS-011 | Folder-based type deletion is always kept | OFFLINE | PASS | `test_prune` | Report and Dashboard deletions stay in destructiveChanges; the lister is never called for them. |
| TS-012 | Case/URL-encoding difference keeps the deletion | OFFLINE | PASS | `test_prune` | Org has `My_Label`: `My%5FLabel` and `my_label` are kept (deleted); only `Absent` is skipped. |
| TS-013 | "Nothing left" after pruning succeeds without deploying | OFFLINE (+LIVE) | PASS | `test_deploy_action` (rendered run block: rc 0, `mode=nothing-left`, `sf` never called); live in TS-010 | |
| TS-014 | Gated mode: below-threshold Apex never reaches the org | LIVE | BLOCKED-CLASSIFIER | command below; offline `test_deploy_action` | On the approval-gated `int` the validation gate stops <85% Apex before any deploy, so gated-mode-fails needs a `gate: auto` stage. Plan: temporarily set `mon` (`env/monday`, protected, gh_environment `int`) to `gate: auto`, open PR #105 (PROJ-109, 80%) into it, revert. The admin push to `env/monday` was **denied by the auto-mode classifier**; nothing was pushed (`env/monday` still `e33f96e`). Offline: a real Apex deploy with the gate runs `project deploy validate` (`mode=gated`), and the quick-deploy step's `if:` requires `steps.coverage.outcome == 'success'`. **To finish:** `bash scratchpad/ws1/monday.sh auto`, push any commit to a PR from `feature/PROJ-109-gated` into `env/monday` (reopen #105), expect the deploy run to log `Aggregate Apex coverage: 80.00%` and skip "Quick-deploy the validated job", `sf org list metadata -m ApexClass -o sfp-int` without PROJ109, then `bash scratchpad/ws1/monday.sh restore`. |
| TS-015 | Gated two-job race (quick-deploy refused) | LIVE | PASS (+NEW-WS1-01, NEW-WS1-03 fixed) | PRs #93/#94/#91 approved together at 00:51:28; #94 deploy 40611426762; retry 40611940869 | #94 validated `0Afh1000000gQAzCAM`, passed the gate (85.00%), then `Salesforce refused the quick deploy … nothing was deployed` (SF: "Job ID can't be used for quick deployment") because #93/#91 landed in between. Explicit failure, `PROJ104` absent from the org afterwards, no merge. Remove+re-add `approved` validated afresh, deployed and merged. The PR report blamed the coverage gate (NEW-WS1-01) and the `approved` label had to be removed by hand first (NEW-WS1-03) — both fixed. |
| TS-016 | Apex class name `$(…)` runs on the runner (BUG-001) | LIVE | FAIL→FIXED (`b439e8b`) | pre-fix: offline render of the real step created the marker (pre-fix suite: "a test name with $(...) runs nothing ✗"); post-fix: PR #95 (PROJ-106, `Mk$(echo WS1INJECT)Test.cls`), validation 40610732795 | Post-fix the run fails at "Resolve specified tests": `Not a valid Apex class or trigger name: 'Mk$(echo WS1INJECT)Test' …`, rendered in the PR report. The expansion `MkWS1INJECTTest` occurs 0 times in the full log. PR closed. |
| TS-017 | `untested=` path (BUG-001 variant) | OFFLINE | FAIL→FIXED (`b439e8b`) | `test_deploy_action` | `UNTESTED='Mk$(touch INJECTED)'`: pre-fix the marker was created; post-fix rc 1, no marker, message quotes the literal name. |
| TS-018 | Fix verification — outputs via `env:` are inert | OFFLINE | PASS | `test_deploy_action`, `test_specified_tests` | No `run:` block of `sf-pipeline/deploy/action.yml` contains `${{ }}` (checked); `TESTS='Mk$(touch INJECTED)Test,Other\`touch INJECTED\`Test'` reaches `sf` as two literal `--tests` values; hostile names (`$()`, back ticks, space, `;`, leading digit) are refused by `specified_tests.py` with exit 1. |
| TS-019 | `production.destructive_tests` with `$(…)` (BUG-005) | OFFLINE | FAIL→FIXED (`b439e8b`) | `test_deploy_action` | `CANARY_TESTS='Canary$(touch POC)Test'`, ALIAS=prod, Apex deletion: pre-fix the marker was created; post-fix no marker, `--test-level RunSpecifiedTests --tests 'Canary$(touch POC)Test'`. |
| TS-020 | `node_version` traversal is rejected (BUG-007) | OFFLINE | FAIL→FIXED (`b439e8b`) | `test_unsafe_values` | `../../etc`, `/usr/bin`, `22/../..` rejected; `22`, `22.x`, `20.11.1`, `lts/*`, `lts/iron`, `latest` still accepted. |
| TS-021 | PR relaxing its own coverage gate is ignored | LIVE | PASS | PR #96 (PROJ-103); run 40610818000 | The PR's own config said `apex_coverage: false`, `coverage_threshold: 50`; plan logged `gates and environments from 'int'`, `q_apex_coverage = true`, `coverage_threshold = 85`; failed at 85. |
| TS-022 | PR on an unprotected base falls back to default branch | LIVE | PASS | helper `ws1/ts022-unprotected-helper` (its config: `ws1helper` stage, `gate: auto`, `NoTestRun`, `apex_coverage: false`); PR #99 (PROJ-110); deploy run 40610883865 | `##[notice]'ws1/ts022-unprotected-helper' is not a protected branch — reading the pipeline config from the default branch 'main'` → `r_gate = none` → `proceed=false`; every downstream job skipped, nothing deployed. (pr-validation does not trigger for that base — the caller filters `int` / `release/**`.) PR closed. |
| TS-023 | `source_path` with shell metacharacters rejected | OFFLINE | PASS | `test_unsafe_values` | `force-app; echo x` and an embedded newline → non-zero exit. |
| TS-024 | Single-environment chain resolves | OFFLINE | PASS | `test_minimal_config` | `entry_branch`, `last_env`, `release_label=deployed:sandbox`, `r_require_env(s)=""`, `r_is_entry=true`, resolve → `approval`. |
| TS-025 | `name != branch` resolve-by-name returns inert | OFFLINE | PASS | `test_resolve_by_branch` | `resolve tue` (a name; branch is `env/tuesday`) → `r_name=""`, `r_is_stage=false`, rc 0. |
| TS-026 | Admin bypass honoured; non-admin ignored | LIVE (+HUMAN) | BLOCKED-HUMAN (part a PASS) | (a) PR #98 (PROJ-107, 80%): reopen validation 40611965102, deploy 40612276765 | (a) Admin-applied `bypass-quality-gates`: validation `Admin bypass — … NoTestRun (coverage gate skipped)` → success; approved → `Deploying to int (test level NoTestRun)` → merged, `deployed:int`. (b) needs a write-not-admin identity: the repo's only collaborator is the admin owner. **To finish (b):** invite a collaborator with `write`, have them add `bypass-quality-gates` to a <85% Apex PR into `int` and push/reopen; expect the `bypass` job to report it inactive and the validation to fail at 85%. |
| TS-027 | Push after bypass drops it (stale) | LIVE | PASS | PR #98; synchronize run 40611458119 | Bypass added, then commit `6861332` pushed: label removed and comment "🔒 **Admin bypass removed** — new commits were pushed after `bypass-quality-gates` was added …"; validation ran with the gates (failure). |
| TS-028 | `approved` before green is stripped | LIVE | PASS | PR #91: approve-guard 40610649810, deploy 40610649629; later 40611429686 | Added 11 s after opening: "⏳ **Approval held** — … (`pr-validation` = `pending`)", label removed; the deploy guard also refused (`pr-validation`=`pending`). Re-added after green: kept, deployed, merged. |
| TS-029 | Self-approval honoured only with `allow_self_approval` | LIVE | PASS | true: PR #91 (author's own label on `int`) → run 40611429686 deployed + merged. false: PR #105 into `env/monday` (protected; its config has no `allow_self_approval`) → run 40611490574 | false path: "🚫 @muraliseelam, this is your own PR, so your `approved` label can't approve its deploy to **mon** — the label was removed …" — although the PR's **own** copy of the config (from `main`) says `allow_self_approval: true`. PR closed. |
| TS-030 | audit-log commit is unresolvable (BUG-004) | LIVE (read) | FAIL→FIXED (`b439e8b`) | `scratchpad/ws1/audit_check.sh` | Before: `int` records at 00:35–00:36Z (PRs 76–79) have `.sha` MISSING (merge ref) while `.ref` resolves; 72 of the first 78 records have `sha != ref`. After the push: every new record has `sha == ref`, resolvable (93 records at 01:12Z, still 72 mismatches — all from before the fix) — PRs 77/78/71/65/82 (other WS) and WS1's #93 (`59b187bd`), #94 (`dfe57e77`, both the refused and the successful run), #91 (`39998d55`), #98 (`6861332e`), #108 (`2875b148`). |
| TS-031 | Fix verification — `.sha` equals the deployed commit | OFFLINE | PASS | `test_audit`, `test_delta` (`to_sha == HEAD`), `test_deploy_action` (no `AUDIT_SHA` uses `github.sha`) | |
| TS-032 | `specified_tests.py` has a unit suite (BUG-006) | OFFLINE | FAIL→FIXED (`b439e8b`) | `test_specified_tests` in `tests/test_deploy_engine.py` (run by `validate.sh` §7b and therefore by `ci.yml`) | Name matching (`<C>Test`, `<C>_Test`, `Test<C>`, `<C>Tests`), trigger, self-test, `@isTest`/`testMethod` detection, `TestDataFactory`, no-match → `untested` + `RunLocalTests`, hostile names. Put in the existing deploy-engine suite (WS1-owned, already wired) instead of a new file, so no `validate.sh` change was needed. |
| TS-033 | Apex deletion to prod: canary / RunLocalTests, never NoTestRun | OFFLINE | PASS | `test_deploy_action` | prod + Apex deletion, check-only and real: no canary → `RunLocalTests`; canary → `RunSpecifiedTests --tests CanaryTest`; both with `--post-destructive-changes`; a sandbox deletion may use `NoTestRun`. |
| TS-034 | Profiles incremental self-diffs against `from_ref` | OFFLINE | PASS | `test_profiles_incremental` | Target branch changed `B`, this branch changed `A`: against the merge-base only `Account.A__c` is kept; against the target's tip `B` would be deployed back. (Running it locally exposed a Windows-only `git show` path bug in `profiles.py`, fixed in `526f8e5`; CI runners were unaffected.) |

## Findings owned by WS1 (from the report)

| BUG | Result | Fix | Verified |
|---|---|---|---|
| BUG-001 (critical) | FIXED | `b439e8b`: every value reaches the deploy action's shell through `env:` (no `${{ }}` left in any of its `run:` blocks); `specified_tests.py` refuses a changed class/trigger whose name is not `^[A-Za-z][A-Za-z0-9_]*$` (exit 1, message in the PR report) | live PR #95 / run 40610732795; offline TS-017/018 |
| BUG-004 | FIXED | `b439e8b`: `delta.sh` outputs `to_sha` (the commit diffed to); both audit steps use `steps.delta.outputs.to_sha \|\| inputs.ref` | live audit-log records after the push; TS-031. Records written before the fix keep the merge SHA in `sha` (append-only log, not rewritten): look those up by `ref`. |
| BUG-005 (fix part) | FIXED | `b439e8b`: `CANARY_TESTS` from `env:` | TS-019 |
| BUG-006 | FIXED | `b439e8b`: `test_specified_tests` | TS-032 |
| BUG-007 | FIXED | `b439e8b`: `node_version` rejects a leading `/` and `..` | TS-020 |

## New findings

### NEW-WS1-01 — A refused quick deploy in gated mode is reported as a coverage stop
- **Severity:** LOW · **Category:** report accuracy · **Owner:** WS1 · **Status:** CONFIRMED live, FIXED `526f8e5`
- **Exposed by:** TS-015 (PR #94, run 40611426762)
- **Where:** `sf-pipeline/deploy/action.yml` "Build deployment report" passed `--mode gated`;
  `deploy_report.py` then says "the change is validated with its tests and coverage-gated
  before it is deployed, and it stopped there" although the gate had **passed** (85.00%) and
  Salesforce refused the quick deploy because another deploy reached the org.
- **Fix:** report mode `quick` when a gated deploy's quick deploy failed; the quick wording now
  explains the 10-day / unchanged-org rule and says to run the deploy again (on a PR: approve
  again). Offline: `test_deploy_action` (both gated failure shapes).

### NEW-WS1-02 — `prod-release.yml` interpolates the dispatch `version` into `run:`; a crafted version empties the release proof
- **Severity:** MEDIUM · **Category:** injection / release integrity · **Owner:** WS3 · **Status:** CONFIRMED offline, OPEN (cross-workstream request 1)
- **Where:** `.github/workflows/prod-release.yml` job `plan` step `ref`
  (`echo "ref=${{ steps.cfg.outputs.release_prefix }}${{ inputs.version }}" …`) and job
  `assemble` step "Determine version + configure git" (`V="${{ inputs.version }}"`). Still
  present after WS3's `069095f`.
- **Failure scenario:** anyone who can dispatch Prod Release (write access) passes
  `version: x" ; echo chain_statuses= >> "$GITHUB_OUTPUT" ; echo ref=release/x >> "$GITHUB_OUTPUT" ; exit 0 ; echo "`.
  The step runs it and its outputs become `chain_statuses=` (empty) and `ref=release/x`;
  `release-gather` then gets `require_statuses: ""`, i.e. the proof that each gathered story
  was deployed to every environment is skipped. Arbitrary code also runs with the
  workflow's `contents: write` / `pull-requests: write` token.
- **Evidence:** `bash scratchpad/ws1/new02.sh` renders the real step the way the runner
  does → `step rc=0`, GITHUB_OUTPUT = `chain_statuses=` / `ref=release/x`.
- **Fix:** see cross-workstream request 1.

### NEW-WS1-03 — `approved` survived a failed deploy, so re-adding it did nothing
- **Severity:** LOW · **Category:** operability · **Owner:** WS1 · **Status:** CONFIRMED live, FIXED `65fc385`
- **Exposed by:** TS-015 — after the refused quick deploy, PR #94 still carried `approved`;
  adding it again fires no `labeled` event, so the retry needed remove + add (the report and
  the User Guide say "approve again").
- **Fix:** `deploy.yml` deploy job: "Drop approved after a failed deploy"
  (`if: failure() && steps.deploy.outcome == 'failure'`), as the moved-head and
  merge-failure paths already do.
- **Verified live (after the push):** PR #108 (PROJ-111, test with `SeeAllData=true` that fails
  while a marker Account exists). Validation green (40612775107); marker Account created in
  `sfp-int` (setup); `approved` → deploy run 40613149347 failed in the gated validation
  (`PROJ111Test.noBlocker - … Expected: 0, Actual: 1`, nothing deployed) and
  `github-actions[bot]` removed `approved` at 01:09:29Z. Marker deleted, `approved` added
  again → run 40613404185 deployed and merged.

### NEW-WS1-04 — `profiles.py` only finds Profiles / Permission Sets under `<source_path>/main/default`
- **Severity:** LOW · **Category:** portability · **Owner:** WS1 · **Status:** CONFIRMED (code + offline), FIXED `65fc385`
- **Where:** `resolve_target()` built `<source_root>/main/default/{profiles,permissionsets}`
  only. With another layout (`source_path: src`, `src/profiles/…`) incremental mode printed
  "source not found, left as-is" and deployed the profile **whole** — the failure mode
  incremental exists to avoid. Now falls back to searching the source root; offline checks
  for a flat profile, a decomposed permission set and a missing one.

### NEW-WS1-05 — Sandbox GitHub Environments have no deployment-branch policy
- **Severity:** MEDIUM (consumer setup; LOW for scratch orgs) · **Category:** secret exposure · **Owner:** WS3 (setup kit / docs) · **Status:** CONFIRMED (read-only), not exploited
- **Evidence:** `gh api repos/<consumer>/environments` → `int`, `qa`, `stage` (and `mon/tue/wed`)
  have `deployment_branch_policy: null`; only `prod` / `prod-validate` are limited to `main`.
- **Impact:** BUG-001 is closed, but a writer can still read the sandbox orgs' JWT secrets
  without review — a `pull_request` run uses the PR's own copy of the caller workflow, and
  any job may declare `environment: int`. This bounds what "PR validation runs before
  approval" means for secrets and should be stated in the security docs; restricting the
  environments to protected branches would also block the pipeline's own `pull_request`
  validation job (its ref is `refs/pull/<n>/merge`), so this is a documented trade-off, not
  a code fix. (The policy semantics for `pull_request` runs were not tested live.)

## Fixes pushed (pipeline `main`)

| sha | change |
|---|---|
| `b439e8b` | Deploy: keep Apex test names out of the shell (all deploy-action `run:` values via `env:`; non-Apex class names refused); audit the deployed commit (`to_sha`); `node_version` rejects paths; `specified_tests.py` suite + rendered-run-block injection tests (BUG-001/004/005/006/007) |
| `526f8e5` | Deploy report: say when Salesforce refused a gated deploy's quick deploy (NEW-WS1-01); `profiles.py` git tree path uses `/`; tests for TS-004/011/012/024/025/033/034 |
| `65fc385` | Deploy: drop `approved` after a failed deploy (NEW-WS1-03); `profiles.py` finds profiles outside `main/default` (NEW-WS1-04) |

## Cross-workstream requests

1. **WS3 — `.github/workflows/prod-release.yml` (NEW-WS1-02).** Replace the `plan` → `ref` step's
   first line and add two env entries:
   ```yaml
      - id: ref
        env:
          EARLIER: ${{ steps.last.outputs.r_require_envs }}
          LAST_ENV: ${{ steps.cfg.outputs.last_env }}
          RELEASE_PREFIX: ${{ steps.cfg.outputs.release_prefix }}
          VERSION: ${{ inputs.version }}
        run: |
          case "$VERSION" in *[!A-Za-z0-9._-]*) echo "::error::version '$VERSION' may use only letters, digits and . _ -"; exit 1 ;; esac
          echo "ref=${RELEASE_PREFIX}${VERSION}" >> "$GITHUB_OUTPUT"
   ```
   and in `assemble` → "Determine version + configure git" add `VERSION: ${{ inputs.version }}`
   to its `env:` and change the first line to `V="$VERSION"; [ -z "$V" ] && V="$(date -u +%G.%V)"`.
   A regression check: no `run:` block of `prod-release.yml` contains `${{` (as
   `test_deploy_action` does for the deploy action).
2. **WS3 — docs (see *Doc changes*).**

No `validate.sh` hook is needed: the new checks live in `tests/test_deploy_engine.py` and
`tests/test_config_matrix.py`, which `validate.sh` already runs.

## Doc changes needed (WS3 owns docs)

1. `docs/Product_Documentation.md` §13 *Test selection*, append:
   > Test names come from the Apex file names in the change. A changed class or trigger whose
   > name is not an Apex identifier (letters, digits and underscores, starting with a letter)
   > fails the validation before anything else runs, and the PR report says which.
2. `docs/Product_Documentation.md` §16 *Audit trail*, after item 3:
   > Each record's `sha` / `short_sha` is the commit that was deployed (for a PR deploy the
   > same commit as `ref`). Records written before 2026-10-28 carry a pull_request run's
   > temporary merge commit in `sha`; look those up by `ref`.
3. `docs/Product_Documentation.md` §Config (the paragraph ending "plain relative path /
   version characters only, or the run fails."), add:
   > `node_version` takes what `actions/setup-node` accepts (`22`, `22.x`, `20.11.1`,
   > `lts/*`, `lts/iron`, `latest`); a leading `/` or `..` is refused, as for `source_path`.
4. `docs/Pipeline_User_Guide.md` *Salesforce refused the quick deploy …*: replace "for an
   entry deploy, approve again (or remove and re-add `approved`)" with
   > for an entry deploy, add `approved` again (a failed deploy removes it) or approve again
5. Security section (Product_Documentation / SECURITY.md): a sentence for NEW-WS1-05 —
   > Sandbox environments (`int`, `qa`, `stage`) are usable by any workflow in the repository,
   > including a pull request's own copy of the caller workflows, so their org credentials are
   > trusted to everyone with write access. Only `prod` / `prod-validate` are limited to `main`.
6. `docs/Product_Documentation.md` §14 *Profiles*: "Profiles and permission sets are found
   anywhere under `source_path` (not only `main/default`)."

## Consumer cleanup

- PRs: #91, #93, #94, #98, #108 merged into `int` by the pipeline (`deployed:int`; none
  reached `deployed:stage`, so no `hold` needed); #95, #96, #97, #99, #105, #107 closed.
  Stray `bypass-quality-gates` removed from #98. No WS1 PR is open and no WS1 run is in
  progress.
- `sfp-int` data: the PROJ-111 marker Account (`001h10000065b6xAAA`) was created for the
  NEW-WS1-03 check and deleted again.
- Branches created and left in place (the brief forbids deleting remote branches):
  `feature/PROJ-101-field`, `-102-apex100`, `-103-lowcov-relaxcfg`, `-104-exact85`,
  `-105-notest`, `-106-inject`, `-107-bypass`, `-108-delete-absent`, `-109-gated`,
  `-110-unprotected-base`, `-111-deploy-fails`, and `ws1/ts022-unprotected-helper`
  (unprotected; its config is never trusted).
- `int` org: PROJ101__c, PROJ102(+Test), PROJ104(+Test), PROJ107(+Test), PROJ111(+Test)
  deployed through the pipeline (normal story flow). No settings, protection, environments or labels changed;
  `env/monday` untouched (the push was denied). Check-only calibration validations in
  `sfp-int` deployed nothing.

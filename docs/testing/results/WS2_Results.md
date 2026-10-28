# WS2 results — Promotion, state & ops runbooks (TS-035 … TS-067)

**Engineer:** WS2 · **Date:** 2026-10-28 (UTC) · **Consumer:** `muraliseelam/sfdx-pipeline-example`
(own clone, callers on `@main`) · **Stories:** PROJ-201 … PROJ-215 (+ `PROJ-290-recreate`,
`PROJ-299-foo/bar` from the Recreate runbook) · **Orgs:** `sfp-int`, `sfp-qa`, `sfp-stage`

Pipeline commits under test: the promotion scenarios ran on `8fd014f` (plus WS1's `b439e8b`
once it landed); the runbook fixes are `e55e029`, and every runbook scenario was re-run live
after it was pushed.

## Counts

| Result | Count |
|---|---|
| PASS | 30 |
| FAIL→FIXED | 2 (TS-065; TS-066 released-change path — the unreleased path stays OPEN, see NEW-WS2-02) |
| FAIL→OPEN | 0 at scenario level — the unreleased-change residual of TS-066 is kept OPEN under NEW-WS2-02 |
| BLOCKED-HUMAN | 0 |
| NOT-APPLICABLE (live) | 1 (TS-054 — no NoTestRun stage in the live consumer; PASS offline) |
| **Total** | **33** |

Offline suite (new): `tests/test_promotion_ops.py` — **62 checks**, all pass on `e55e029`. Against
the pre-fix files (`8fd014f`) it fails **19** checks (TS-065 names, the reset record, rollback cut
from the baseline, `ref` validation, `profiles_mode`, the Summary interpolation, Jira keys); dropping only
`-X no-renames` from the fixed rollback makes the look-alike check fail. `PYTHONUTF8=1 bash
scripts/validate.sh` → *All checks passed* (on `65fc385` + my commit). `actionlint -shellcheck=`
clean on the four workflows; `shellcheck` clean on every extracted `run:` block (LF copies).

## Scenarios

PR and run numbers are in `muraliseelam/sfdx-pipeline-example`.

| TS | title | mode | result | evidence | notes |
|---|---|---|---|---|---|
| TS-035 | entry deploy happy path | LIVE | PASS | #65 (PROJ-201): validation run 40609009311 green → `approved` 00:32:23 → deploy run 40609397296 ctx→plan→changes→bypass→guards→deploy→merge; `deploy-int=success` by `github-actions[bot]`; squash-merged at `dc455d6`; `deployed:int` stamped; `approved` cleared. `sfp-int` tooling query: `WS2_201` on Account. | Also #68/#70/#71/#72/#73 the same way. `hold` added once it reached stage. |
| TS-036 | commit pushed after approval refused pre-deploy | LIVE | PASS (deviation → NEW-WS2-01) | #66 (PROJ-202): `approved` 00:32:54, new commit `695adfc` pushed 00:32:55. Run 40609450344: **guards** job failed — "this PR's head is now `695adfcf`, but the commit approved for this deploy is `8bc9dd1a`"; no `deploy-int`, `WS2_202` not in `sfp-int`. The `synchronize` run 40609461638 deployed nothing. | The refusal came from the guard (it runs before the deploy job), not `Refuse if the PR moved`, so **`approved` stayed on the PR** — see NEW-WS2-01. |
| TS-037 | commit pushed DURING deploy → deployed-not-merged | LIVE | PASS | #67 (PROJ-203): deploy step in progress 00:34:03, push 00:34:26. Run 40609473223: deploy success (`sfp-int` `WS2_203` created 00:34:25), merge job failed: "its head moved to `4b41aff` after `edad995` was deployed"; no `deployed:int`; comment "The `int` org has `edad995`, but this run merged nothing"; `approved` removed. | Recovery verified: re-approved at `4b41aff` → merged, `deployed:int` (org and branch agree again). |
| TS-038 | merge-conflict PR blocked before the org | LIVE + OFFLINE | PASS | #75 (PROJ-204 edits `CustomLabels`, which `int` deleted): `mergeable=CONFLICTING`; `approved` added → **no workflow run at all** (GitHub starts no `pull_request` workflow for a conflicting PR); nothing deployed. Guard conflict branch: `test_promotion_ops.py` "a conflicting PR is refused before the org". | The guard's conflict refusal can only fire live when the conflict appears after the run started. |
| TS-039 | `mergeable=UNKNOWN` after retries is a refusal | OFFLINE | PASS | `test_promotion_ops.py`: guard precheck exit 1 "mergeability is 'UNKNOWN'" after 8 tries; promote `prepare` refuses and closes the PR it opened. | |
| TS-040 | ordering trusts only bot `deploy-<env>` | OFFLINE | PASS | `test_promotion_ops.py`: ordering guard with a `deploy-int` POSTed by `muraliseelam` → blocked "deploy-int=missing"; `Record why` names the missing stage. Live corroboration: TS-050. | |
| TS-041 | deploy:qa happy path | LIVE | PASS | #65 `deploy:qa` 00:38:30 → run 40610042245: env PR #87 opened at `dc455d6`, ordering guard passed, `sfp-qa` deploy, merged, `deployed:qa` on #87 and #65, `deploy:qa` cleared, ✅ comment. | Then `deploy:stage` → run 40610938230, #102 merged, `deployed:stage`, `hold` added. |
| TS-042 | deploy:stage requires int AND qa | LIVE | PASS | #65 `deploy:stage` before qa (run 40609852784): "must be deployed **and** merged in every earlier stage first (`int, qa`). Not yet: `qa` (`deploy-qa`=missing)". Same on #71 (run 40609973445). | |
| TS-043 | `deploy:int` on a merged PR | LIVE | PASS | #65 run 40609841077: "❌ `deploy:int` is not a promotion", label removed. | |
| TS-044 | `deploy:prod` / unknown env | LIVE | PASS | Runs 40609843574 / 40609846399: `##[error]'prod' is not a configured environment. Valid: int, qa, stage` (and `'nope'`); `report_failure` commented, labels removed. | Temporary labels `deploy:prod`, `deploy:nope` deleted afterwards. |
| TS-045 | `deploy: qa` (space) rejected | LIVE | PASS | Run 40609849291: `'deploy: qa' is not a 'deploy:<env>' label (no spaces).`; comment + label removed. | Cosmetic: the control-PR comment is the generic "could not be resolved" text ("Promotion to `␠qa`"), the "no spaces" reason is only in the log. Label deleted afterwards. |
| TS-046 | feature tip moved past merged head | LIVE | PASS | #68 (PROJ-205): `bdb6f26` pushed after the int merge; `deploy:qa` → "Promotion to `qa` refused — `feature/PROJ-205-moved-on` has moved on" with both ways forward; no env PR opened. | |
| TS-047 | env-PR conflict surfaces on control PR | LIVE | PASS | Setup: admin push of `ws2/qa-conflict.txt` to `qa` (528bca5). #73 (PROJ-206, same path) `deploy:qa` → env PR #83 created, CONFLICTING → comment on #73 linking #83, #83 closed, `deploy:qa` removed, nothing deployed. | Setup reverted on `qa` (d5682a9). |
| TS-048 | idempotent re-add after `deployed:qa` | LIVE | PASS | #65 run 40610490952: prepare → "ℹ️ `deploy:qa`: nothing to do — `dc455d6` is already deployed to `qa` and merged"; deploy/merge skipped. | |
| TS-049 | hand-added `deployed:qa` not trusted | LIVE | PASS | #69 (PROJ-207): `deployed:qa` added by hand, then `deploy:qa` → run 40610932305 `##[notice]#69 carries deployed:qa but 8b749847 has no deploy-qa=success — label not trusted; promoting.` → deployed + merged (#101). | |
| TS-050 | forged `deploy-qa` ignored | LIVE | PASS | #72 (PROJ-210): `deploy-qa=success` POSTed as `muraliseelam`; `deploy:stage` → "Not yet: `qa` (`deploy-qa`=missing)". | The forged status cannot be deleted (statuses are immutable); it is ignored everywhere (non-bot). |
| TS-051 | expired PAT → "could not start" | OFFLINE | PASS | `test_promotion_ops.py`: `gh pr create` stderr "HTTP 401: Bad credentials" → refusal naming `PIPELINE_PAT`, `reported=true`, `deploy:qa` removed, no PR closed/left. | |
| TS-052 | "No commits between" is benign | OFFLINE | PASS | `test_promotion_ops.py`: → `skip=true`, exit 0, "nothing to do" comment. | |
| TS-053 | gated deploy fails → org and branch agree | LIVE | PASS | #116 (PROJ-215, 81.82% coverage; int via admin `bypass-quality-gates`): `deploy:qa` → run 40614771504, gated: validation passed Salesforce's 75%, the pipeline gate stopped it — control-PR note **"The coverage gate failed on the validation, so nothing was deployed or merged — the `qa` org and `qa` still agree."**, aggregate 81.82% < 85%, env PR #118 closed, `deploy:qa` removed; `sfp-qa` has no `WS2Mid215` (tooling query). Offline: `test_promotion_ops.py` both notes. | Below 75% (#106 18%, #114 71%) Salesforce rejects the validation before the pipeline gate runs; the note is then the generic "Nothing was merged." while the error block says nothing was deployed (runs 40613328527, 40614154286). |
| TS-054 | NoTestRun stage skips the coverage gate | LIVE | NOT-APPLICABLE live · PASS offline | The live consumer's `qa`/`stage` set no `test_level`, so they resolve to **RunSpecifiedTests** (`plan.py:101`), not NoTestRun: #69's qa promotion ran **gated** with the coverage gate (run 40610932305: `COVERAGE_GATE: true`, `MODE: gated`). Offline: `coverage_gate` expression in `sf-promote-label.yml` + `deploy/action.yml` (`gated` requires `LEVEL != NoTestRun`). | The report's premise is wrong (doc change below). A live check needs a consumer config with `test_level: NoTestRun` on a stage — not changed here because WS3's qa promotions share that config. |
| TS-055 | control PR not merged → refused | LIVE | PASS | #114 while still OPEN: "Promotion to `qa` refused — #114 is still open — it has not been deployed and merged into `int` yet". | |
| TS-056 | two PRs promoting into one org concurrently | LIVE | PASS | #69 run 40610932305 (00:46:48→00:48:18) and #70 run 40610934193 (00:46:49→00:48:35) overlapped; both deployed and merged (#101, #100); neither cancelled. | Predicted UNKNOWN. |
| TS-057 | deploy:qa + deploy:stage together | LIVE | PASS | #71 (PROJ-209) both labels 00:37:51: stage refused (run 40609973445, `deploy-qa`=missing), qa promoted (run 40609973359, #85); re-added `deploy:stage` → run 40610488095, #89 merged, `deployed:stage`; `hold` added. | |
| TS-058 | label re-added mid-run queues then no-ops | LIVE | PASS | #65: first run 40610042245 (00:38:31→00:39:54); `deploy:qa` removed+re-added 00:39:15 → run 40610122400 queued on the same group, then "nothing to do" (00:40:11). One deploy. | |
| TS-059 | hand-posted `pr-validation` ignored | LIVE | PASS | #74 (PROJ-211): `pr-validation=success` POSTed as `muraliseelam`, `approved` added before the real validation finished → approve-guard "⏳ Approval held … `pr-validation` = `pending`", label removed; deploy run's guard "PR Validation isn't green". | |
| TS-060 | `deployed:<env>` is a record, not an instruction | LIVE | PASS | #72: `deployed:stage` added by hand 00:39:24 → promote 40610137044, deploy 40610137038, approve-guard 40610137069 all **skipped**. Label removed again so WS3's release gather never sees it. | Release-gather trust is WS3's TS-079. |
| TS-061 | Redeploy dry_run default is check-only | LIVE | PASS | Run 40610523447 (`feature/PROJ-201-base` → int): `MODE: dry-run`, "Successfully validated against `int` (check-only)", summary "check-only (dry run)", no branch moved. Re-run after `e55e029`: 40613553778 (→ qa) — "gates and environments from 'main'", summary written from env. | |
| TS-062 | profiles_mode override applies | LIVE | PASS | `feature/PROJ-212-permset` (field + new PermissionSet): run 40610578592 `profiles_mode=incremental` → `PROFILES_MODE: incremental`, "PermissionSet WS2_212_PS: NEW on this branch -- deploying in FULL (initial)", audit `profiles_mode` incremental; control run 40610581931 (config) → "profiles_mode=full -- deploying COMPLETE". | |
| TS-063 | Redeploy unknown environment | LIVE | PASS | Run 40610527599: `'nope' is not a configured environment. Valid: int, qa, stage`. | |
| TS-064 | Recreate existing branch needs confirm_reset | LIVE | PASS | Run 40610596266 created `feature/PROJ-290-recreate`; run 40610884664 (no confirm) refused "Re-run with confirm_reset=true"; after a commit (`2c78e68`) run 40613592512 (`e55e029`, confirm) reset it to main with `--force-with-lease` and recorded "Resetting existing branch … (2c78e68…)" + *Previous tip* row. | |
| TS-065 | weird name normalised safely | LIVE | FAIL→FIXED (`e55e029`) | Pre-fix run 40609523015: `Target branch: feature/PROJ-299-foo/../bar` → `fatal: invalid refspec`. Post-fix run 40613539269: `Target branch: feature/PROJ-299-foo/bar`, created. Offline: 6 names normalised, `..`/`/`/`@{}`/empty refused. | Decision: keep `.` and `/`, **drop** empty/`.`/`..`/dot-led/dot-ended/`.lock` path parts (never resolve `..` — `a/../b` must not become `b`, an existing branch a reset would wipe), then `git check-ref-format`. `echo` → `printf` (`-n` came out empty). |
| TS-066 | Rollback: unmerged refused; merged → revert PR | LIVE | (a) PASS · (b) FAIL→FIXED for a released change, OPEN for an unreleased one | (a) run 40610609177 "PR #66 is not merged". (b) pre-fix #92 (revert of one commit) validated a package of **17** components — every int-only story. Post-fix: rollback of release PR #60 (run 40613546331) → #113 "on top of `main`", package **0 deploy · 3 delete** (exactly the release's HardenedGreeting/Test + E2E_Hardened), validated green; rollback of unreleased #72 (run 40613542723) → #112 cut from `int` with the new warning; its package carries **27** int-only components and its validation fails on another story's Apex. | NEW-WS2-02. #113 was not deployed: it would remove HardenedGreeting from the shared int baseline. |
| TS-067 | conflicting revert stops with instructions | LIVE | PASS | Pre-fix run 40610824884: "Revert of 695adfc… hit conflicts against 'int'. Resolve manually: git checkout -B rollback/695adfcf origin/int && git revert 695adfc…" — but the conflict was in **WS2_201**__c (rename detection matched WS2_202's change to a look-alike file). Post-fix run 40613550299: modify/delete conflict on the right file (`WS2_202__c`), commands include `-X no-renames`. | NEW-WS2-03. |

## New findings

### NEW-WS2-01 — `approved` survives a guard refusal (head moved / PR not open)
- **Severity:** LOW · **Owner:** WS1 (`deploy.yml` / `sf-pipeline/guard`) · **Status:** CONFIRMED live (#66)
- **Where:** `deploy.yml` `guards` job → `guard` "PR state + merge-conflict precheck". When the PR
  moved between approval and the guards job, the guard comments and fails, the deploy job is
  skipped, and nothing removes `approved` (`Refuse if the PR moved` and WS1's new *Drop approved
  after a failed deploy* only run inside the deploy job).
- **Effect:** the PR shows `approved` on an unapproved head; adding the label again is a no-op
  (no `labeled` event), so the documented "re-add `approved`" does nothing until it is removed.
  Nothing deploys (approval is event-driven), so this is UX, not a bypass.
- **Fix:** see cross-workstream request CW-1.

### NEW-WS2-02 — A rollback cut from the entry branch carries every unreleased change on it
- **Severity:** HIGH · **Owner:** WS2 · **Status:** CONFIRMED live; **FIXED** for released changes (`e55e029`); residual OPEN
- **Where:** `sf-ops-rollback.yml` cut `rollback/<sha>` from `origin/$BASE` (the entry branch).
- **Failure:** every deploy delta is computed against `main` (three-dot), so the revert PR's
  package is *the whole of int's divergence from production* plus the revert. Live: #92 (revert
  of one commit) → 17 components; #112 → 27 components including other workstreams' Apex, and its
  validation failed on an unrelated story's coverage. Promoting such a PR (`deploy:qa`,
  `deploy:stage`) would merge and deploy all of them into qa/stage and on into a release — the
  same ride-along class as BUG-002, from a runbook the docs present as the safe path.
- **Fix (`e55e029`):** the revert is cut from the production baseline whenever it applies there
  (the change is in production), so the PR carries ONLY the revert (#113: 3 deletions). Only when
  it does not (never released, or changed in production since) does it fall back to `$BASE` —
  the old behaviour — and then the PR body and the run say how many files it also carries and not
  to promote it.
- **Residual (OPEN — design decision):** for a change that never reached production the revert
  can only be expressed against `int`, so its package stays contaminated under
  `delta_baseline: prod` (and, per the User Guide, it cannot remove the component from the org
  anyway). Options: refuse that case under `delta_baseline: prod` (tell the user to `hold` the
  story and delete the component by hand), and/or have `sf-promote-label` refuse a `deploy:<env>`
  on a control PR whose head is a `rollback/*` branch cut from an env branch.

### NEW-WS2-03 — Rollback's revert used rename detection (could edit a look-alike component)
- **Severity:** MEDIUM · **Owner:** WS2 · **Status:** CONFIRMED live + offline; **FIXED** `e55e029`
- **Failure:** `git revert` (ort) detects renames. Reverting a change to a file the base does not
  have lets git match a *similar* file instead — Salesforce field files are near-identical, so
  this is likely. Live run 40610824884 reverted WS2_202's change into **WS2_201**__c (it happened
  to conflict); the offline harness reproduces a *clean* revert that edits the wrong field.
- **Fix:** `git revert -X no-renames` — a revert touches only the files the change touched.

### NEW-WS2-04 — Redeploy: caller text in the script; gates from the dispatch ref
- **Severity:** LOW · **Owner:** WS2 · **Status:** CONFIRMED (code + offline); **FIXED** `e55e029`
- The `Summary` step interpolated `inputs.feature_ref` into its `run:` script (the shell-injection
  class of BUG-001, here for whoever can dispatch). `profiles_mode` is a free `string` in the
  reusable workflow and reached the deploy action unchecked. The gates (`apex_coverage`,
  `test_level`, environment mapping) were read from whatever branch the run was dispatched from,
  so a writer could relax them for a REAL deploy by dispatching from a branch with an edited
  config — the PR flows read gates from a protected base for exactly this reason.
- **Fix:** env for the summary; `profiles_mode` ∈ {blank, full, incremental}; `config_ref:
  ${{ github.ref_name }}` (the plan action falls back to the default branch for an unprotected ref).

### NEW-WS2-05 — Rollback `ref` accepted any text
- **Severity:** LOW · **Status:** FIXED `e55e029`. `ref` went to `git fetch origin "$SHA"` and into
  the branch name; a branch name or `--option` is not "a commit SHA" (the documented input). Now
  `^[0-9a-fA-F]{7,40}$`, resolved to the full SHA.

### NEW-WS2-06 — `jira.py` used an unvalidated issue key in the API path
- **Severity:** LOW · **Status:** FIXED `e55e029`. `story_key` (an action input) became part of
  `…/rest/api/3/issue/<key>/…`; `../`-style keys could reach other endpoints with the Jira token.
  Now only `[A-Z][A-Z0-9]+-\d+` is used; anything else is a skip-with-warning.

### Observations (not fixed)
- **Gated quick deploy vs. a busy entry org:** with 9 stories approved at once, #69's gated
  deploy had its quick deploy refused because another deploy changed `sfp-int` between validate
  and quick deploy (run 40609401989). Re-approving succeeded. WS1's `526f8e5`/`65fc385` now
  explain it and drop `approved`; the race itself is inherent to validate→quick-deploy.
- Recreate's concurrency group uses the raw `name`, so two spellings of one branch
  (`PROJ-1`, `feature/PROJ-1`) don't serialise. Low impact (the reset is now lease-pinned).
- Re-running a rollback of the same commit force-updates an existing `rollback/<sha8>` branch
  (lease against the fresh fetch); an open PR on it would change head.

## Fixes pushed

| sha | one line |
|---|---|
| `e55e029` | Ops runbooks: safe branch names (TS-065), rollbacks cut from the baseline + `-X no-renames` + SHA-only `ref` (NEW-WS2-02/03/05), Redeploy env-only summary / `profiles_mode` check / protected-branch gates (NEW-WS2-04), Jira key check (NEW-WS2-06); new `tests/test_promotion_ops.py` (62 checks). |

## Cross-workstream requests

- **CW-1 → WS1 (`.github/workflows/deploy.yml`, `guards` job)** — NEW-WS2-01. Add after the
  guard step, mirroring WS1's *Drop approved after a failed deploy*:
  ```yaml
      # A refused deploy (PR moved, closed, conflicting, out of order, validation
      # not green) drops `approved`, so adding it again starts a new deploy.
      - name: Drop approved after a refused deploy
        if: ${{ failure() }}
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
          PR: ${{ needs.ctx.outputs.pr_number }}
        run: gh api -X DELETE "repos/$REPO/issues/$PR/labels/approved" >/dev/null 2>&1 || true
  ```
  (`guards` already has `pull-requests: write`; on a `gate: auto` PR there is no label — harmless.)
- **CW-2 → WS3 (`scripts/validate.sh`, `.github/workflows/ci.yml`)** — wire the new suite in,
  next to 7b:
  ```bash
  head2 "7c. Promotion & ops runbooks"
  # The real run blocks of promotion prepare, guard, merge-pr and the ops
  # runbooks, against local git repos with a fake gh. Offline.
  if [[ -f tests/test_promotion_ops.py ]]; then
    if OUT=$(python3 tests/test_promotion_ops.py 2>&1); then
      pass "$(grep -oE 'All [0-9]+ checks passed' <<< "$OUT" || echo 'promotion & ops suite passed')"
    else
      fail "promotion & ops suite failed:"
      grep -E '✗' <<< "$OUT" | sed 's/^/    /'
    fi
  else
    fail "tests/test_promotion_ops.py is missing"
  fi
  ```
  and in `ci.yml`, beside `python3 tests/test_config_matrix.py`: `python3 tests/test_promotion_ops.py`
  (needs `git`, `bash`, `jq`, PyYAML — all on `ubuntu-latest`).

## Doc changes needed (WS3 owns docs)

- **User Guide, Part 4 → Ops — Recreate Feature Branch, `name` row:** "Feature name or Jira key;
  normalised to `feature/<name>` — spaces become `-`, characters other than letters, digits,
  `.`, `_`, `/`, `-` are dropped, and path parts git refuses (empty, `.`, `..`, starting or ending
  with `.`, ending in `.lock`) are removed — never resolved, so `a/../b` becomes `a/b`, not `b`."
  And under the table: "Resetting an existing branch records its previous tip in the run summary
  — the only way back to the discarded commits."
- **User Guide, Part 4 → Ops — Redeploy Feature Branch:** add "The gates and environments come
  from the branch you dispatch from only if it is protected; otherwise from the default branch,
  so a branch can't relax the coverage gate for a real deploy."
- **User Guide, Part 4 → Ops — Rollback**, replace the first paragraph's mechanics with: "The
  revert is cut from the production baseline whenever it applies there, so the PR carries only
  the revert and can be promoted like any story. A change that never reached production can't be
  reverted on the baseline; the revert is then cut from `base` and **also carries every change on
  `base` that production doesn't have** — the PR says so; don't promote it with `deploy:<env>`."
  `ref` row: "a commit SHA (7–40 hex characters)". Add: "Renames are not followed: the revert only
  touches the files the change touched. If `base` is behind the baseline, the PR also brings
  those baseline commits into `base`, as Sync Env Branches would."
- **Product_Documentation §18** (Rollback row): same two sentences.
- **Pipeline_Test_Report.md TS-053/TS-054:** "qa is `on_demand`/`NoTestRun` in the live consumer"
  is wrong — `qa`/`stage` set no `test_level`, so they run **RunSpecifiedTests** (`plan.py`
  default) and a promotion of Apex is gated. TS-053 needs coverage between 75% (Salesforce's own
  minimum, which fails the validation first) and `coverage_threshold`.

## Consumer cleanup

- Labels: temporary `deploy:prod`, `deploy:nope`, `deploy: qa` deleted; hand-added
  `deployed:stage` removed from #72; `hold` on every story that reached stage (#65, #71).
- `qa`: the TS-047 setup file removed again (admin push d5682a9).
- PRs: test artefacts closed — #66, #74, #75, #92, #112, #113 (and #83/#110/#115/#118 closed by
  the pipeline). #67 re-approved so `sfp-int` and `int` agree after TS-037. No WS2 PR left open.
- Config/protection: unchanged by WS2 (no config or protection edits were needed).
- Left in place (remote branches can't be deleted from here; user may delete):
  `feature/PROJ-201-base` … `feature/PROJ-215-gate-82pct`, `feature/PROJ-290-recreate`,
  `feature/PROJ-299-foo/bar`, `rollback/a4e87999`, `rollback/bebfa062`, `rollback/b88360b8`.
  Org content from these stories stays in the scratch orgs (`WS2_2xx__c` fields,
  `WS2Promo207`, `WS2Low213`, `WS2Mid214`, `WS2Mid215` in int; 201/207/208/209 in qa; 201/209 in
  stage). #106/#114/#116 (low-coverage Apex) are int-only, merged with the admin bypass label.

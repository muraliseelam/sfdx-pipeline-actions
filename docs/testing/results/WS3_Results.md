# WS3 results — Release, sync & platform (TS-068 … TS-100)

**Engineer:** WS3 · **Date:** 2026-10-28 (UTC) · **Consumer:** `muraliseelam/sfdx-pipeline-example`
(own clone, callers on `@main`) · **Stories:** PROJ-301 … PROJ-306 · **Releases:** 2026.300 – 2026.306,
2026.399 · **Orgs:** `sfp-int/qa/stage` through the pipeline; `sfp-prod` only by check-only
validation (`prod-validate`) — **no production deploy was approved**; every release run was
cancelled at the `prod` reviewer gate.

Pipeline commits: `069095f` (BUG-003, BUG-002) was pushed after the pre-fix live evidence was
taken and before the post-fix live re-runs; `e9f9d1b` / `492e2aa` followed (see *Fixes pushed*).

## Counts

| Result | Count |
|---|---|
| PASS | 28 |
| FAIL→FIXED | 4 (TS-079, TS-080, TS-081, TS-099) |
| BLOCKED-HUMAN | 1 (TS-086) |
| **Total** | **33** |

Partial remainders that need a human are noted on TS-078, TS-081 and TS-090 (their automatable
part passed). Offline: new `tests/test_release_train.py` (69 checks, validate.sh §12b) and
extended `tests/test_branch_protection.py` (72 checks). Run against the pre-fix files
(`scratchpad/ws3/prefix_check.sh`, ref `8fd014f`) the release-train suite fails 10 checks (all
BUG-002 and BUG-003 checks) and stops at the missing deploy-job guard.
`PYTHONUTF8=1 bash scripts/validate.sh` → *All checks passed* before each push; pipeline CI
(validate, ShellCheck, actionlint) green on `069095f` and `492e2aa`.

## Scenarios

Run / PR numbers are in `muraliseelam/sfdx-pipeline-example`.

| TS | title | mode | result | evidence | notes |
|---|---|---|---|---|---|
| TS-068 | assemble gathers proven stories, skips shipped | LIVE | PASS | runs 40611278223 (`release/2026.300` ← PROJ-303 @ `ef13c643`), 40612786001 (`release/2026.304` ← PROJ-304 @ `b06b6e13`); 8 shipped stories skipped with notices (40612438945) | Compare link printed, PR not opened (opened by hand: #103, #109). Explicit versions used: a blank version is `date -u +%G.%V` = 2026.44, a release branch that already exists on the consumer, so it was not dispatched |
| TS-069 | nothing to release is an error | LIVE | PASS | run 40608790482: "No live release branches remain…", no branch pushed | |
| TS-070 | explicit `stories` still requires proof | LIVE | PASS | run 40613414359: "feature/PROJ-305-int-only: not deployed to every stage … deploy-qa=missing, deploy-stage=missing" | PR #79 (int only) |
| TS-071 | stale release branch with an unaccounted commit | LIVE | PASS | run 40613469803: "release/2026.301 already contains commits … cb6f30a"; branch left at `cb6f30a` | hand commit pushed for setup |
| TS-072 | hand-added `deployed:stage` ignored | LIVE | PASS | run 40612438945: "'feature/PROJ-305-int-only' carries 'deployed:stage' on a commit the pipeline never deployed … NOT released" | label removed afterwards |
| TS-073 | forged bot-name status not trusted | OFFLINE | PASS | `test_release_train.py::test_gather` — "the one set by @mallory is not trusted", story left out | live too: WS2's PROJ-210-forged excluded in 40612438945 |
| TS-074 | `hold` on any of a story's PRs holds it | LIVE | PASS | run 40612438945: `hold #78 -> feature/PROJ-304-apex`, `hold #86` (its stage env PR), not gathered | hold removed after the run |
| TS-075 | deleted story branch drops out | OFFLINE | PASS | `test_gather`: "Skipping 'feature/GONE'", survivors returned | |
| TS-076 | newest unshipped proven commit chosen | OFFLINE | PASS | `test_gather`: S4 releases the follow-up, not the shipped commit; S1's later commit named, not released; S2 releases its proven commit, not the int-only rebase | |
| TS-077 | unauthorized release PR refused | LIVE | PASS | run 40612627571: "Release PR #103 is not authorized"; validate/deploy skipped | |
| TS-078 | stale Approve refused; label re-authorizes | LIVE + OFFLINE | PASS | live: re-assembly (40612531680) removed `prod-approved` with a PR comment; re-adding it → preflight passed (40612762497). Offline: `test_preflight` (older-commit approval refused; label re-authorizes) | **BLOCKED-HUMAN remainder:** a formal Approve review needs a second GitHub user: have user B approve release PR, push one commit to its branch, run `release` → expect "approved, but not at its current commit"; add `prod-approved` → passes |
| TS-079 | held story rides in (BUG-002) | LIVE | FAIL→FIXED `069095f` | **pre-fix** 40610945588: `release/2026.302` (only PROJ-302-a gathered) contains `PROJ301_B__c` of held PROJ-301-b. **post-fix** 40612438945 (auto gather) and 40613546420 (`stories=` A): "would ship changes of stories on hold: feature/PROJ-301-b-held: …PROJ301_B__c"; nothing pushed | Live variant: A (#82) merged held B's **branch**. The `git merge origin/int` variant was run offline only (TS-080): live it would have carried WS2's in-flight int stories (#71/#72/#73) to qa/stage |
| TS-080 | assemble guard vs ride-along | OFFLINE | FAIL→FIXED `069095f` | `test_assemble`: A merged `int` → refused, naming int and B's squash; A merged B's branch → refused, naming B's file; clean A → base + A only. Pre-fix: these checks fail | |
| TS-081 | hotfix on main after assemble deleted (BUG-003) | LIVE | FAIL→FIXED `069095f` | **pre-fix** 40611485698 (hotfix PR #104 merged after assemble): preflight passed although behind main; "Computing delta origin/main..HEAD" (two-dot); `destructiveChanges` = `Account.PROJ303Hotfix__c`, dropped only because the org lacks it ("Skipped deleting … not in the 'prod-validate' org"). **post-fix** 40612327906: "Release PR #103 is 2 commit(s) behind main" — refused, nothing validated; re-assembled (40612531680); 40612762497: "Using merge-base 75ad4c2 … (three-dot)", package `Account.PROJ303_Rel__c` only, no deletion; cancelled at the gate | **BLOCKED-CLASSIFIER:** putting the hotfix into `sfp-prod` so the pre-fix deletion would have hit a real component (`sf project deploy start --source-dir force-app/main/default/objects/Account/fields/PROJ303Hotfix__c.field-meta.xml --target-org sfp-prod`) was denied ("Production Deploy"). The realistic path is a human-approved hotfix release — see *Remaining human steps* |
| TS-082 | three-dot prod delta drops the phantom deletion | OFFLINE + LIVE | PASS | `scratchpad/ws3/ts082.sh` (real `delta.sh` + sfdx-git-delta 6.45.1 on harness e1): two-dot → destructive `Account.Hotfix__c`; three-dot → none. Live 40612762497 | |
| TS-083 | `destructive_tests` `$(…)` on prod deploy (BUG-005) | OFFLINE | PASS | after WS1's `b439e8b`: no `${{ }}` left in any `run:` of `sf-pipeline/deploy/action.yml`; `CANARY_TESTS` comes from `env:` (`scratchpad/ws3/ts083.py`); WS1's rendered-run-block test covers a hostile canary | config/doc side: see BUG-005 below |
| TS-084 | no-Apex release validated check-only, then full deploy | LIVE-HUMAN | PASS (to the gate) | 40611485698 and 40612762497: "Validated … with a check-only deploy (job 0Afhv000000jFpKCAU, no Apex tests) … FULL deploy"; `quick=false`; chat: "✅ Prod validation PASSED … (check-only, no Apex tests; the deploy will be a full deploy)" | cancelled at the gate |
| TS-085 | Apex release captures a quick-deploy id | LIVE-HUMAN | PASS (to the gate) | 40613175833: RunSpecifiedTests `PROJ304GreetingTest`, `deploy_id=0Afhv000000jMScCAM`, `quick=true`; audit record `sha` = release head `c00b2214` | cancelled at the gate; PR #109 closed |
| TS-086 | refused quick deploy stops before merge/tag | LIVE-HUMAN | BLOCKED-HUMAN | needs an approved prod deploy | Steps: assemble an Apex release, open + `prod-approved` the PR, run `release`; after validate, land any other deploy on `sfp-prod` (or wait >10 days); approve the `prod` gate → expect "Salesforce refused the quick deploy …", no merge, no tag, summary with the safe retry |
| TS-087 | second release on a `deployed:prod` PR refused | LIVE | PASS | 40613089690: "Release PR #103 carries 'deployed:prod' … would deploy it a second time" | label added by hand for the test, then removed |
| TS-088 | re-run of finalize skips a merged PR | OFFLINE | PASS | `test_finalize`: "already merged", no `gh pr merge`, tag + release created; re-run idempotent | |
| TS-089 | branch moved after preflight → merge refused | OFFLINE | PASS | `test_finalize`: merge refused at `--match-head-commit` → step fails, no tag, no GitHub Release | |
| TS-090 | sync-branches protection round-trip + trap (BUG-008) | LIVE-read + OFFLINE | PASS | live: `int` `deploy-int,pr-validation@15368`, `qa`/`stage` `deploy-<env>@15368`, unchanged. Offline `test_realign`: qa's PUT refused (full + minimal) → reported against **qa only**, int/uat restored each with its **own** snapshot, step fails; `scratchpad/ws3/trapkill.sh`: the EXIT trap still restores the current branch on SIGTERM/SIGINT | BUG-008 not reproduced. **BLOCKED-HUMAN remainder:** a live round trip needs a finalized release (prod approval) |
| TS-091 | env branch with unreleased work gets a merge | OFFLINE | PASS | `test_realign`: int → merge commit by sf-release-bot, parents [old int, main] | |
| TS-092 | conflicting env branch left as is | OFFLINE | PASS | `test_realign`: stage untouched, "left as is" warning | |
| TS-093 | Sync Env Branches falls back to a PR | LIVE→OFFLINE | PASS | `test_sync_env_branches` runs the real step: push refused by a pre-receive hook → sync branch pushed + `gh pr create --base int --head sync/main-into-int-<run>`; PR creation refused → warning, job not failed | Not dispatched live: the consumer has `PIPELINE_PAT` (admin push succeeds, so the fallback can't trigger without deleting that secret — a secret write), and a live sync would move int/qa/stage under WS2's running promotions |
| TS-094 | Project Init dry run then apply | LIVE | PASS | 40612824941 (dry: everything "exists"), 40613059904 (apply: nothing created, nothing deleted) | |
| TS-095 | prod envs limited to the production branch | LIVE-read | PASS | `prod`, `prod-validate`: custom policy, `main` only | |
| TS-096 | release/* cannot use prod secrets | LIVE | PASS | PR #111 into `release/2026.304`; run 40613499282: "Branch "refs/pull/111/merge" is not allowed to deploy to prod-validate", job ran 0 steps | PR closed |
| TS-097 | every pipeline branch PR-only + app-pinned | OFFLINE + LIVE-read | PASS | live as TS-090; `main` PR-only, no status, no force-push/deletion; `test_branch_protection.py` 72 checks | |
| TS-098 | multi-config portability | OFFLINE | PASS | `test_config_matrix.py` (weekday chain, one env, `release_label` override); new `test_chain_sizes` (1 and 6 stages, prefixed branches, auto entry) and `test_project_init_dry_run` (branches by branch name with slashes, labels by env name, env names default, prod envs limited to the prod branch, no-prod config) | |
| TS-099 | `@vX` tag rewrites all internal refs | OFFLINE | FAIL→FIXED `e9f9d1b` | before: `cut-release.sh` crashed on Windows (`UnicodeDecodeError: 'charmap' … 0x8f`, cp1252) before tagging. After: 51 refs in 12 files rewritten, `verify-tag.sh` ✓, 0 `@main` in the tag's `deploy.yml`, only `uses:`/`SELF` lines changed | on Linux it passed before the fix |
| TS-100 | org bootstrap kit at the pinned ref; ECA fallback | OFFLINE | PASS | the cut tag's `org-bootstrap.yml` has `SELF: …/org-bootstrap.yml@v9.9.9-ws3`; `push-env-secrets.sh` PUTs an environment only when GET says it is missing; `bootstrap-org.sh` falls back to `metadata-eca/` on "create a connected app" | a real bootstrap needs an org admin login |

## Findings owned by WS3

| BUG | result | how |
|---|---|---|
| **BUG-003** (HIGH) | **FIXED `069095f`**, verified live (TS-081) + offline (TS-082) | Both prod deploy calls pass `delta_merge_base: "true"`; preflight refuses a release PR that is behind the prod branch via the compare API (GitHub reports `BEHIND` only under strict protection — the consumer's `main` has none, so the old `BEHIND` acceptance never even fired); the deploy job re-checks after the reviewer's approval ("Prod branch unchanged since preflight") so a hotfix merged while the gate waited is not overwritten |
| **BUG-002** (MEDIUM→HIGH) | **FIXED `069095f`**, verified live (TS-079) + offline (TS-080) | Assemble checks the **content**: (1) a gathered commit may not contain commits of an env branch that the prod branch lacks (`git merge origin/int`, a branch recreated from int); (2) no file a story on `hold` changed may carry that story's version in the assembled tree unless `main` already has it (catches a merged held branch or a cherry-pick). `release-gather` outputs `held`. Residual (documented): a story that merged another *non-held* story's branch ships it — that change went through every stage as part of the story |
| **BUG-005** (config/doc) | **DONE `492e2aa`** | WS1's `b439e8b` passes the canary through `env:`; `pipeline.config.example.yml` and Product_Documentation §13 now say `destructive_tests` takes Apex class names only, comma-separated. A format check in `plan.py` is a cross-workstream request |
| **BUG-008** (LOW) | **NOT REPRODUCED** — no code change | The trap is armed after the snapshot and cleared after each explicit restore, `restore` always returns 0, and every `continue` comes before the trap is armed, so nothing stays armed across iterations. Offline (TS-090) a refused restore is reported against the right branch only, others restore their own snapshot, the step fails; the trap also fires on SIGTERM/SIGINT. `test_realign` now guards it |

## New findings

### NEW-WS3-01 — Redeploy Feature Branch diffs two-dot: a branch older than main deletes main's newer components
- **Severity:** MEDIUM · **Category:** delta / data loss (ops runbook) · **Owner:** WS2 (`.github/workflows/sf-ops-redeploy-feature.yml`) · **Status:** CONFIRMED (same `delta.sh` path as BUG-003), OPEN — cross-workstream request 1
- **Where:** `sf-ops-redeploy-feature.yml` calls `sf-pipeline/deploy` without `delta_merge_base` (still so after WS2's `e55e029`), so the delta is `origin/main..<feature_ref>`.
- **Failure scenario:** redeploy (`dry_run: false`) a feature branch cut before a release that added component X → X (present in the org, absent from the branch) is in `destructiveChanges.xml`; `prune_destructive.py` keeps it because the org has it → X is deleted from int/qa/stage.
- **Evidence:** `scratchpad/ws3/ts082.sh` (real delta.sh): `DELTA_MERGE_BASE=false` → destructive `Account.Hotfix__c`; `true` → none.

### NEW-WS3-02 — `cut-release.sh` crashed on Windows before tagging
- **Severity:** LOW · **Category:** portability (maintainer tooling) · **Status:** FIXED `e9f9d1b` (TS-099)
- `Path.read_text()` used the OS code page (cp1252) and the workflows contain emoji → `UnicodeDecodeError`; `write_text` would also have rewritten line endings. Now bytes in, bytes out (UTF-8).

### NEW-WS3-03 — A preflight refusal posted "❌ Prod validation FAILED" to chat
- **Severity:** LOW · **Category:** operability / misleading signal · **Status:** FIXED `e9f9d1b`, verified live 40614875493
- `validate-notify` runs `always()`; with `validate` skipped it fell through to the FAILED text. It now says "⛔ Prod release refused for v… before validation (see preflight) — nothing was validated or deployed."

### NEW-WS3-04 — Docs said the reference consumer uses `delta_baseline: target`
- **Severity:** LOW · **Category:** docs-vs-behaviour · **Status:** FIXED `492e2aa` — it uses `prod` on main/int/qa/stage.

NEW-WS1-02 (WS1's finding in a WS3 file: the dispatch `version` pasted into `run:`) — **FIXED `e9f9d1b`**, verified live: run 40614588433 refused `version='x" ; echo chain_statuses= >> "$GITHUB_OUTPUT" ; echo "'` at `plan`; a normal version still assembles (40614660091). All WS3 workflows/actions now keep `${{ }}` out of `run:` (Project Init's summary and Sync Env Branches' branch / run id moved to env too); `test_no_expressions_in_run` enforces it.

## Fixes pushed (pipeline `main`)

| sha | change |
|---|---|
| `069095f` | Release train: three-dot prod delta, preflight refuses a release behind the prod branch, deploy job refuses if it moved during the gate (BUG-003); assemble refuses env-branch passengers and held content, `release-gather` outputs `held` (BUG-002); Sync Env Branches branch via env; new `tests/test_release_train.py` + validate.sh §12b |
| `e9f9d1b` | Dispatch `version` through env and validated (NEW-WS1-02); Project Init summary via env; refusal chat message (NEW-WS3-03); `cut-release.sh` encoding (NEW-WS3-02); tests: no expressions in run blocks, 1/6-stage chains, Project Init dry run |
| `492e2aa` | Docs: passenger refusal, never behind main, three-dot everywhere, version characters, consumer uses `prod`; WS1's doc changes (test-name refusal, audit `sha`, `node_version` values, `approved` removed after a failed deploy, profiles anywhere under `source_path`, `destructive_tests` format, sandbox-environment trade-off in SECURITY.md) |
| this commit | validate.sh §12c runs WS2's `tests/test_promotion_ops.py` (62 checks, passing) — hooked ahead of a request; results file |

## Cross-workstream requests

1. **WS2 — `.github/workflows/sf-ops-redeploy-feature.yml` (NEW-WS3-01).** In the `Deploy … to …` step's `with:` add, after `baseline_branch:`:
   ```yaml
             delta_merge_base: "true"
   ```
   (Redeploy is then three-dot like every other deploy; Product_Documentation §13 "Every delta is three-dot" can then list it.)
2. **WS1 / lead — `sf-pipeline/scripts/plan.py` (BUG-005 config side).** Validate `production.destructive_tests` like the code keys, e.g. `re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*\s*(,\s*[A-Za-z][A-Za-z0-9_]*\s*)*)?$")`, failing the plan with "destructive_tests: Apex class names, comma-separated". The value no longer reaches a shell unquoted, so this is defence in depth.

## Doc changes

All made in `492e2aa` (User Guide, Product Documentation, sf-pipeline/README, SECURITY.md, example config). After WS2's results land: document `e55e029` (rollback revert cut from the baseline when the change reached production; `ref` must be a SHA; recreate name rules) and NEW-WS3-01 once fixed.

## Remaining human steps (production gate)

- **BUG-003, real deletion (TS-081 remainder):** assemble two releases from `main` (A with a new field, B with another story); release and approve A at the `prod` gate; then run `release` for B without re-assembling → expect preflight "is N commit(s) behind main". Re-assemble B, re-authorize, run → validate shows only B's components and no deletion of A's field.
- **TS-078 Approve half, TS-086, TS-090 live round trip:** steps in the table rows above.

## Consumer cleanup done

- Release PRs #103 (2026.300) and #109 (2026.304) closed, `prod-approved`/`deployed:prod` removed; PR #111 (TS-096) closed; every release run cancelled at the gate (40611485698, 40612762497, 40613175833). No run of WS3 in progress.
- Hotfix PR #104 (TS-081 setup) merged into `main`, then reverted by PR #117 (`522479e`), so `main` again matches what production has (the hotfix never reached `sfp-prod`).
- `hold` on every WS3 story at `deployed:stage` (#77 PROJ-303, #78 PROJ-304, #82 PROJ-302) and on #76 (PROJ-301-b); #79 (PROJ-305, int only) has no release label.
- Unchanged, verified at the end: branch protection (int `deploy-int,pr-validation@15368`, qa/stage `deploy-<env>@15368`, main PR-only, 0 reviews), `prod`/`prod-validate` limited to `main`, config identical on main/int/qa/stage (`6f0146d`), no settings changed.
- Left in place (remote branches are never deleted by WS3): `feature/PROJ-301-b-held`, `PROJ-302-a-merges-b`, `PROJ-303-rel-field`, `PROJ-304-apex`, `PROJ-305-int-only`, `PROJ-306-into-release`, `hotfix/PROJ-303-hotfix`, `revert/PROJ-303-hotfix`, `release/2026.300`, `.301`, `.302`, `.304`. The int/qa/stage orgs and branches keep the WS3 test fields/classes (PROJ301_B, PROJ302_A, PROJ303_Rel, PROJ304Greeting(+Test); PROJ305_X on int).
- Local leftover: `C:\t3\cr`, a clone made for TS-099 (the scratchpad path is too long for `sf-setup/metadata-eca` files); removing it was refused by the safety check — delete it by hand.

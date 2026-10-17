# Configuration reference

Everything the pipeline does is driven by one file: [`sf-pipeline/pipeline.config.yml`](../sf-pipeline/pipeline.config.yml).
Workflows, composite actions and scripts read it through `pipeline_config.py`, so nothing about your
orgs, branches, secrets or quality gates is hardcoded anywhere else.

```bash
python sf-pipeline/scripts/pipeline_config.py validate   # check an edit
python sf-pipeline/scripts/pipeline_config.py show       # print the fully resolved config (all defaults filled in)
```

## The minimum

The only required section is the list of environments **in promotion order**. Feature PRs enter at
the first one; the last one is production.

```yaml
version: 1
environments:
  dev: {}
  int: {}
  qa: {}
  stage: {}
  prod: {}
```

Four stages? Drop one line. With `int, qa, stage, prod`, feature PRs enter at `int`. Any names work
(`sit`, `uat`, `preprod`, `uat-2`…): letters, digits, `-` and `_`, starting with a letter.
Two to six environments are supported.

`bootstrap.py init --envs int,qa,stage,prod` rewrites just this block for you.

## Per-environment settings and their defaults

Every key is optional. `<NAME>` is the env name in upper case with non-alphanumerics turned into `_`
(`uat-2` → `UAT_2`).

| Key | Default (non-production) | Default (production) | Meaning |
|---|---|---|---|
| `branch` | `<name>` | `project.baseline_branch` (`main`) | Long-lived git branch for the env |
| `org_alias` | `<name>` | `<name>` | Alias the org is logged in under (locally and in CI) |
| `auth_method` | `auth.method` | `auth.method` | `sfdx_url` or `jwt` (see [Authentication](authentication.md)) |
| `secrets.auth_url` | `SF_<NAME>_AUTH_URL` | same | Secret holding the SFDX auth URL (`sfdx_url`) |
| `secrets.username` | `SF_<NAME>_USERNAME` | same | Integration username (`jwt`) |
| `secrets.consumer_key` | `SF_<NAME>_CONSUMER_KEY` | same | App consumer key (`jwt`) |
| `secrets.jwt_key` | `SF_<NAME>_JWT_KEY` | same | PEM private key (`jwt`) |
| `instance_url` | `https://test.salesforce.com` | `https://login.salesforce.com` | Login URL for JWT (My Domain URLs work too) |
| `status_check` | `deploy-<name>` | `deploy-<name>` | Required commit status posted by validation |
| `test_policy` | `specified` | `local` | Apex test level policy (below) |
| `canary_tests` | `[]` | `[]` | Smoke tests always run when `test_policy: canary` |
| `required_approvals` | `approvals.required_reviews` | same | Reviews that count as approval (the label always does) |
| `github_environment` | `sf-<name>` | `production` | GitHub Environment the real deploy runs in |
| `validation_environment` | `sf-<name>-validate` | `production-validate` | (production) Environment for the check-only validation |
| `sync_after_release` | `true` | `false` | Reset this branch to `main` after each release |
| `jira_status` | `""` | `""` | Jira status to move linked stories to after a deploy (needs `notifications.jira`) |
| `production` | `false` | `true` | Set on one env only if production isn't listed last |

### Test policies

| Policy | Test level | Use for |
|---|---|---|
| `specified` | `RunSpecifiedTests` with the companion tests of every changed class/trigger (and changed test classes); `NoTestRun` when the delta has no Apex | Sandboxes (default) |
| `local` | `RunLocalTests` | Production (default) |
| `canary` | `RunSpecifiedTests` with companions **plus** `canary_tests` | Production with a fast smoke suite |
| `none` | `NoTestRun` | Sandboxes only |

Production never receives `NoTestRun`: a production delta without Apex runs `RunLocalTests`.
In every policy except `none`, a changed class without a resolvable test class fails the run
**before** anything is deployed. The pipeline never quietly falls back to running all tests.

## Top-level sections

### `project`
| Key | Default | Meaning |
|---|---|---|
| `source_path` | `force-app` | Package directory; must be listed in `sfdx-project.json` |
| `baseline_branch` | `main` | Production branch. Every delta is computed from `merge-base(origin/main, HEAD)` |
| `repository` | blank: the repo of the clone you run `bootstrap.py` in | `owner/name` for `bootstrap.py`. Leave it blank; if it names another repo than your clone, `bootstrap.py` stops instead of acting on it |

### `auth`
| Key | Default | Meaning |
|---|---|---|
| `method` | `sfdx_url` | Default auth method for all envs |

### `approvals`
| Key | Default | Meaning |
|---|---|---|
| `label` | `approved` | Adding this label approves a PR (and, on a release PR, starts the production deploy) |
| `required_reviews` | `1` | Approving reviews that count as approval when the label isn't used |
| `dismiss_label_on_push` | `true` | New commits remove the label |
| `status_check` | `sf-approval` | Required commit status reflecting the approval |
| `auto_merge` | `true` | Approved env-branch PRs squash-merge themselves once checks pass |

### `quality`
| Key | Default | Meaning |
|---|---|---|
| `coverage_threshold` | `75` | % line coverage every changed class/trigger must reach (never-executed = 0%) |
| `coverage_exempt` | `[]` | Classes with no executable lines to skip (interfaces and enums are automatic) |
| `code_analyzer.enabled` | `true` | Run Salesforce Code Analyzer on changed files in PRs |
| `code_analyzer.severity_threshold` | `2` | Block on this severity or worse (1 Critical, 2 High, 3 Moderate, 4 Low, 5 Info) |
| `code_analyzer.rule_selector` | `Recommended` | Code Analyzer `--rule-selector` |
| `dependency_lint` | `true` | Fail fast when a deleted (or changed) component is still referenced elsewhere in the source |
| `bypass_label` | `bypass-quality-gates` | Label an admin/maintainer can add to waive the coverage and code-analysis gates (who added it is verified; compile and test failures are never waived) |

### `tests`
| Key | Default | Meaning |
|---|---|---|
| `require_companion` | `true` | Fail fast when changed Apex has no test class |
| `class_patterns` | `{name}Test`, `{name}_Test`, `{name}Tests`, `Test{name}`, `Test_{name}` | Candidate test names for a class |
| `trigger_patterns` | `{name}Test`, `{name}_Test`, `{base}TriggerTest`, `{base}TriggerHandlerTest`, `{base}HandlerTest`, `{base}Test` | For a trigger (`{base}` = name without a trailing `Trigger`) |
| `overrides` | `{}` | Explicit `ClassName: [TestA, TestB]` mappings |

A class counts as a test when `@isTest` precedes its declaration (comments and strings are ignored).

### `release`
| Key | Default | Meaning |
|---|---|---|
| `source_env` | the env before production | Branch the release is cut from |
| `branch_prefix` | `release/` | Release branch prefix |
| `tag_prefix` | `v` | Tag = prefix + version |
| `quick_deploy` | `true` | Deploy production with `sf project deploy quick` against the validated job |

### `promotion`
| Key | Default | Meaning |
|---|---|---|
| `enforce_order` | `true` | A change can only be promoted to the next env, and non-entry envs accept only promotion PRs |
| `branch_prefix` | `promote/` | Promotion branch prefix |

### `deploy`
| Key | Default | Meaning |
|---|---|---|
| `wait_minutes` | `120` | `--wait` for every deploy/validation |
| `delta_output_dir` | `.sgd` | Where sfdx-git-delta writes `package/` and `destructiveChanges/` |
| `ignore_warnings` | `false` | Pass `--ignore-warnings` |
| `profiles_mode` | `full` | `full` deploys whole Profiles/Permission Sets; `incremental` deploys only the entries added or changed since the production baseline |

### `github`
| Key | Default | Meaning |
|---|---|---|
| `pat_secret` | `SF_PIPELINE_PAT` | Token that opens promotion/release PRs and enables auto-merge (so their checks run) |
| `admin_pat_secret` | same as `pat_secret` | Token with Administration rights used by the env-branch sync; can live only in the production validation environment |
| `status_check_app_id` | `-1` | Source allowed to post required statuses (`-1` any; `15368` = GitHub Actions) |
| `labels.promote_prefix` | `promote:` | Promotion label prefix |
| `labels.deploy_prefix` | `deploy:` | Pre-merge deploy label prefix |

### `tooling`
Pinned versions, part of the CLI cache key: `node_version` (`22`), `sf_cli_version`, `sgd_version`
(sfdx-git-delta), `code_analyzer_version`.

### `notifications.chat`
| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Post deploy, release and sync outcomes to chat (a failure only warns) |
| `provider` | `auto` | `slack`, `teams`, `webex`, `generic` (any JSON webhook) or `api`; `auto` detects from the webhook URL |
| `webhook_secret` | `CHAT_WEBHOOK_URL` | Secret holding an incoming-webhook URL |
| `api_url_secret` / `api_token_secret` / `channel_secret` | `CHAT_API_URL` / `CHAT_API_TOKEN` / `CHAT_CHANNEL_ID` | Token-based chat API instead of a webhook (e.g. Webex messages API + bot token + room id) |
| `deploy_success` | `true` | Also announce successful env deploys (failures always are) |

### `notifications.jira`
| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | After each deploy, comment on and transition every Jira key found in the branch name, PR title or release notes to the env's `jira_status` |
| `base_url_secret` / `email_secret` / `token_secret` | `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` | Jira Cloud credentials (best effort; a no-op when missing) |

### `audit`
| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | One JSON record per real deploy (who, what, where, commit, PR, components, coverage, job ID) |
| `branch` | `audit-log` | Branch the records are appended to (`audit/audit-log.jsonl`), independent of Actions log retention |

## Examples

**Four stages with UAT naming and JWT for production only:**

```yaml
environments:
  sit: {}
  uat: {}
  preprod: {}
  prod:
    auth_method: jwt
    instance_url: https://mycompany.my.salesforce.com
auth:
  method: sfdx_url
quality:
  coverage_threshold: 85
```

**Production that isn't called "prod" and keeps a smoke suite:**

```yaml
environments:
  dev: {}
  qa: {}
  live:
    test_policy: canary
    canary_tests: [SmokeTest, CriticalPathTest]
```

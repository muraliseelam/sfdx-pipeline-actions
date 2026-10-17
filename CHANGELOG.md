# Changelog

Changes to the pipeline itself. Tags in a repository that runs the pipeline are that team's
Salesforce releases, so this file, not the tag list, tracks pipeline versions.

## Unreleased

The single-repo pipeline: one config file, everything else derived from your environment names.

### Added
- **Config by convention.** `sf-pipeline/pipeline.config.yml` lists the environments in promotion
  order (2 to 6 stages, any names); branches, org aliases, secret names, status checks and test
  policy follow from the names. `bootstrap.py init` writes the list for you.
- **One-command setup.** `bootstrap.py setup` configures GitHub (branches, labels, environments,
  branch protection), copies your CLI org logins into secrets without printing them, and runs the
  doctor. `bootstrap.py scratch-orgs` creates one scratch org per environment; `jwt-setup` creates
  an External Client App for JWT logins.
- **PR validation.** Delta (sfdx-git-delta from `merge-base(main, HEAD)`), RunSpecifiedTests with
  each changed class's companion test (fail fast when there's none), a coverage gate per changed
  class, Salesforce Code Analyzer on the changed files, and a 🚀 report comment with inline
  annotations.
- **Label-driven flow.** `approved` approves and auto-merges; `promote:<env>` opens the next
  promotion PR; `deploy:<env>` deploys an open PR into an env before it merges;
  `bypass-quality-gates` (admins only) waives the coverage and analysis gates.
- **Two-phase production release.** Prepare validates against production; the `approved` label
  quick-deploys the validated job, merges with a merge commit, tags, publishes a GitHub Release and
  re-syncs the env branches. Every step is resumable and never deploys twice.
- **Operations.** SF Rollback (a revert PR through the pipeline), an `audit-log` branch with one
  record per deploy, Slack/Teams/Webex/webhook notifications, Jira comments and transitions, a
  weekly SF Pipeline Doctor run.
- **Safety nets.** Dependency lint for deleted metadata still in use, incremental profile and
  permission-set deploys, and deletions the target org no longer has are skipped and listed
  instead of failing the deploy. Changes built on an env branch (merges, env rollbacks) also
  delete what they removed from it, so rolling back an unreleased change removes it from that
  env's org.

### Fixed during the end-to-end run
See section X of [the test checklist](docs/TEST_CHECKLIST.md): concurrent auto-merges, release
branch lookup, deletions already done, the self-test depending on a team's own environments,
clearer PR feedback for preflight failures and rejected promotion labels, deploy reports on merged
PRs, rollback attribution, env rollbacks that delete what they added, a template-safe repo
setting, and Windows support in the setup scripts.

## 1.0.0 (2026-10-13)

The original reusable-workflow version of `sfdx-pipeline-actions`, now merged into this repository.

# sf-pipeline — the pipeline implementation

Everything the pipeline actually *does* lives in this folder: the composite
actions, the shared scripts and the config template. The reusable workflows in
[`../.github/workflows/`](../.github/workflows/) are thin orchestration on top of
these pieces, because GitHub requires workflow files to live there.

> **Documentation lives in two places, and only two:**
> - **[../docs/Product_Documentation.md](../docs/Product_Documentation.md)** — how it works: architecture, diagrams, control flow, configuration reference, internals, the full workflow/action/script catalogue.
> - **[../docs/Pipeline_User_Guide.md](../docs/Pipeline_User_Guide.md)** — how to use it: ship a change, promote and release, onboard a new project, ops runbooks, troubleshooting.
>
> This file is just a map of the folder. Please don't duplicate reference
> material here — it drifts.

---

## Folder map

```
sf-pipeline/
├── pipeline.config.example.yml   ← template for a consumer's pipeline.config.yml
│
│   # ---------- composite actions (one folder each, all action.yml) ----------
├── plan/              read the caller's config → outputs (outputs | resolve | prdeploy)
├── changes/           install SF CLI + sfdx-git-delta → delta package + manifest
├── deploy/            auth → profiles → validate/deploy → coverage gate → report → audit
├── guard/             fatal pre-deploy guards: merge conflict, ordering, missing validation
├── approval/          interpret the approval signal
├── bypass/            resolve and re-verify an admin quality-gate bypass at run time
├── merge-pr/          squash-merge the PR after a successful deploy
├── report/            assemble the single sticky PR comment
├── status/            write the pr-validation / deploy-<env> commit statuses
├── coverage/          Apex coverage gate          → coverage-gate.py
├── release-gather/    collect the deployed:stage PRs for a release
├── release-preflight/ verify the release PR exists, is authorized and green
├── sync-branches/     merge main into the env branches (degrades to a PR on conflict)
├── jira/              comment on + transition the story named in the branch
├── notify/            dispatch deploy notifications → scripts/notify.sh
│
└── scripts/           shared helpers, reached by actions via $GITHUB_ACTION_PATH/../scripts
```

### `scripts/`

| Script | Purpose |
|---|---|
| `plan.py` | The config resolution engine — `normalize_envs`, `cmd_outputs`, `cmd_resolve`, `cmd_promote`, `cmd_prdeploy` |
| `delta.sh` | sfdx-git-delta invocation and manifest assembly |
| `auth-jwt.sh` | JWT login to an org using the injected environment secrets |
| `install-sf.sh` | Install the SF CLI and plugins |
| `profiles.py` | `full` / `incremental` profile and permission-set handling |
| `specified_tests.py` | Resolve the Apex test list for `RunSpecifiedTests` |
| `deploy_report.py` | Build the deploy report body |
| `manifest_to_md.py` | Render `package.xml` as a Markdown component table |
| `audit.py` · `audit-append.sh` | Structured audit record + durable append to the `audit-log` branch |
| `jira.py` | Jira comment and transition |
| `dependency_lint.py` | Metadata dependency checks |
| `setup-branch-protection.sh` | Codified, idempotent branch protection |

---

## The one rule when changing anything here

Composite actions are fetched by slug and run from `$GITHUB_ACTION_PATH`, so they
can reach their siblings. **Reusable workflows run in the *caller's* checkout**,
where this folder does not exist — so a workflow must never invoke
`sf-pipeline/scripts/*` directly. It always goes through a composite action.

The only file that *is* read from the caller's workspace is
`sf-pipeline/pipeline.config.yml`, which is why consumers keep it.

See [Product_Documentation.md §3](../docs/Product_Documentation.md#3-portability--how-cross-repo-path-resolution-works).

---

## Setting it up on a new project

One file to edit per project: `pipeline.config.yml`, copied from
[`pipeline.config.example.yml`](./pipeline.config.example.yml).

The full ten-step checklist — callers, config, branches, GitHub Environments and
secrets, branch protection, labels and a smoke test — is in
**[Pipeline_User_Guide.md — Part 3](../docs/Pipeline_User_Guide.md#part-3--onboard-a-new-project)**.

Branch protection is scripted (idempotent; needs `gh` authenticated as a repo
admin):

```bash
REPO=owner/repo bash sf-pipeline/scripts/setup-branch-protection.sh
REPO=owner/repo ENFORCE_ADMINS=true bash sf-pipeline/scripts/setup-branch-protection.sh   # strict
```

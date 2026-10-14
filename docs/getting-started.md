# Getting started

You'll go from an empty repo to a green PR in about 15 minutes. You need:

- A GitHub repo you **admin** (create it from this template, or copy `.github/`, `sf-pipeline/`,
  `config/`, `sfdx-project.json`, `.forceignore`, `.gitattributes` into your own repo).
- Salesforce orgs, one per environment: sandboxes plus production. Or a Dev Hub, so you can try
  everything with scratch orgs.
- Locally: [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli), the
  [GitHub CLI](https://cli.github.com) (`gh auth login`), git, and Python 3.10+ with
  `pip install -r sf-pipeline/scripts/requirements.txt`.

## 1. Name your environments

Edit `sf-pipeline/pipeline.config.yml`, or let the tool do it. List the environments in promotion
order; the last one is production:

```bash
python sf-pipeline/scripts/bootstrap.py init --envs dev,int,qa,stage,prod   # five stages
python sf-pipeline/scripts/bootstrap.py init --envs int,qa,stage,prod       # or four
```

Then set what's specific to you in the same file, typically `quality.coverage_threshold` and
`project.source_path`. Everything else has a sensible default (see [Configuration](configuration.md)).
Commit and push to `main`.

## 2. Log in to your orgs

Once per org, with the alias = env name:

```bash
sf org login web --alias dev   --instance-url https://test.salesforce.com
sf org login web --alias int   --instance-url https://test.salesforce.com
sf org login web --alias qa    --instance-url https://test.salesforce.com
sf org login web --alias stage --instance-url https://test.salesforce.com
sf org login web --alias prod  --instance-url https://login.salesforce.com
```

No sandboxes? `python sf-pipeline/scripts/bootstrap.py scratch-orgs --dev-hub <your-hub-alias>`
creates one scratch org per env, already aliased.

## 3. One command for GitHub + secrets

```bash
python sf-pipeline/scripts/bootstrap.py setup --pat-from-gh
```

`setup` is idempotent; run it again any time. It:

| Step | What it does |
|---|---|
| **github** | Merge settings (squash + merge commit, auto-merge); labels (`approved`, `promote:<env>`, `deploy:<env>`); env branches from `main`; GitHub Environments (`production` and `production-validate` limited to protected branches); branch protection requiring `deploy-<env>` + `sf-approval` |
| **secrets** | `SF_<ENV>_AUTH_URL` for every env from your CLI logins (production's into its protected environments), plus `SF_PIPELINE_PAT` |
| **doctor** | Checks all of the above and prints the fix for anything missing |

Prefer a fine-grained token, prefer JWT, or keep production credentials elsewhere? See
[Authentication](authentication.md).

**What the pipeline needs, at a glance:**

| Secret | Where | Created by |
|---|---|---|
| `SF_<ENV>_AUTH_URL` (or `SF_<ENV>_USERNAME` + `_CONSUMER_KEY` + `_JWT_KEY` for JWT) | Repository; production: the `production` and `production-validate` environments | `bootstrap.py secrets` / `jwt-setup` |
| `SF_PIPELINE_PAT` | Repository | `bootstrap.py secrets --pat-from-gh` or `--pat-stdin` |
| `WEBEX_WEBHOOK_URL` (optional) | Repository | `gh secret set WEBEX_WEBHOOK_URL` |

## 4. Check from GitHub

Run **Actions → SF Pipeline Doctor → Run workflow**. Every environment should log in green.

## 5. Your first change

```bash
git switch -c feature/hello origin/main
# add force-app/main/default/classes/Hello.cls (+ -meta.xml) and HelloTest.cls (+ -meta.xml)
git push -u origin feature/hello
gh pr create --base dev --fill
```

Within a few minutes the PR has:

- **🚀 Salesforce Pipeline Report:** the check-only validation against the dev org with
  `RunSpecifiedTests: HelloTest`, and coverage for `Hello`.
- **🔍 Code Analysis:** the analyzer's findings on the changed files.
- `deploy-dev` ✅ and `sf-approval` ⏳.

Add the **`approved`** label. The PR squash-merges itself and SF Env Deploy deploys it to the dev
org. Then:

- Label the merged PR **`promote:int`**. A promotion PR opens and validates against int. Approve it
  the same way, and repeat for `qa` and `stage`.
- Release: **Actions → SF Prod Release → Run workflow** (on `main`, action `prepare`). When the
  release PR shows *Validated against production*, add **`approved`** to it. Production is deployed,
  `main` is merged and tagged, a GitHub Release is published, and the env branches are re-synced.

That's the whole loop: [How it works](how-it-works.md) explains each step,
[Troubleshooting](troubleshooting.md) covers the messages you might see.

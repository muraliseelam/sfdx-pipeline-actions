# Authentication

The pipeline logs in to each org from GitHub Actions with secrets you store once. Pick a method per
environment (`auth.method` for all, `auth_method` per env). Both are set up by `bootstrap.py`, which
reads your local Salesforce CLI logins, so **secret values never pass through your clipboard and are
never printed**.

| | `sfdx_url` (default) | `jwt` |
|---|---|---|
| Setup | `sf org login web` + one command | One command (`bootstrap.py jwt-setup`) |
| Needs an app in the org | No | Yes: an External Client App, created for you |
| Secrets per env | 1: `SF_<NAME>_AUTH_URL` | 3: `SF_<NAME>_USERNAME`, `_CONSUMER_KEY`, `_JWT_KEY` |
| Works for | Sandboxes, production, scratch orgs | Sandboxes, production, scratch orgs |
| Rotation | Re-run `bootstrap.py secrets` if the refresh token is revoked | Certificate expiry (2 years by default) |
| Best for | Getting started, small teams | Long-lived production setups, no refresh tokens |

## Option A: SFDX auth URL (`sfdx_url`)

1. Log in to each org once, using the env's `org_alias` (by default the env name):

   ```bash
   sf org login web --alias dev  --instance-url https://test.salesforce.com
   sf org login web --alias int  --instance-url https://test.salesforce.com
   sf org login web --alias prod --instance-url https://login.salesforce.com
   ```

   Use the org's My Domain URL instead if your org requires it. Log in as a dedicated integration
   user if you can (System Administrator, or a permission set with *Modify Metadata Through Metadata
   API Functions*, *Author Apex*, *Customize Application* and *API Enabled*).

2. Push the logins into GitHub secrets:

   ```bash
   python sf-pipeline/scripts/bootstrap.py secrets
   ```

   - Already logged in under different aliases? Map them with `--org dev=my-dev-alias`.
   - Production's secret goes into the `production` and `production-validate` GitHub Environments,
     not repository secrets. Those environments only accept protected branches, so no pull request
     or feature-branch workflow can read production credentials. Use `--prod-repo-level` to opt out.

The auth URL embeds a refresh token. It stays valid until the user's password policy, an admin, or
the org revokes it. If a login starts failing (the weekly **SF Pipeline Doctor** run tells you),
re-run step 2.

## Option B: JWT bearer flow (`jwt`)

Since Spring '26 Salesforce blocks creating new **Connected Apps** (even in scratch orgs). JWT now
uses an **External Client App**, which can be deployed as metadata. One command does everything:

```bash
python sf-pipeline/scripts/bootstrap.py jwt-setup --env prod --update-config
```

It:

1. Creates a key pair in `~/.sf-pipeline/<owner>__<repo>/` (never in the repo; valid 2 years) and a
   consumer key per environment.
2. Deploys an External Client App `SF_Pipeline_CI` to the org, using your existing CLI login for the
   env's alias. The app carries the certificate and pre-authorizes the System Administrator profile
   (`--profile` to change).
3. Proves a JWT login works in a throwaway CLI home, retrying while the app propagates. Your own
   logins are untouched.
4. Stores `SF_<NAME>_USERNAME`, `SF_<NAME>_CONSUMER_KEY` and `SF_<NAME>_JWT_KEY`. Production goes
   into its protected environments.
5. With `--update-config`, sets `auth_method: jwt` for the env. Commit that change.

Already have a Connected App or External Client App with a certificate? Then skip `jwt-setup` and use:

```bash
python sf-pipeline/scripts/bootstrap.py secrets --env prod --jwt-key server.key --consumer-key <key>
```

`instance_url` must match the org: `https://test.salesforce.com` (or the sandbox My Domain) for
sandboxes and scratch orgs, `https://login.salesforce.com` (or My Domain) for production.

## The pipeline token (`SF_PIPELINE_PAT`)

GitHub doesn't start workflows for events caused by the built-in `GITHUB_TOKEN`. The pipeline needs a
token for three jobs:

- Opening promotion and release PRs, so that their validation runs.
- Enabling auto-merge, so the merge's push triggers the deploy.
- Lifting and restoring branch protection while env branches are re-synced after a release.

**Recommended:** a fine-grained PAT for this repository only, with read/write access to *Contents*,
*Pull requests*, *Workflows* and *Administration*:

```bash
python sf-pipeline/scripts/bootstrap.py secrets --pat-stdin < token.txt
```

**Quickest:** `--pat-from-gh` stores your GitHub CLI login token. It works immediately, but it's
scoped to all your repositories.

**Hardened:** keep the Administration-capable token out of repository secrets. Set
`github.admin_pat_secret: SF_PIPELINE_ADMIN_PAT`, store that token only in the `production-validate`
environment (`gh secret set SF_PIPELINE_ADMIN_PAT --env production-validate`), and give
`SF_PIPELINE_PAT` only *Contents*, *Pull requests* and *Workflows*.

**Whose name is on the merges.** Merges made with the token (squash merges into env branches, the
release merge into `main`) are authored by the token's owner, with that account's commit email. In a
public repository, turn on *Keep my email addresses private* in the owner's GitHub email settings,
so those commits use the `users.noreply.github.com` address instead of a personal one.

## Checking it all

```bash
python sf-pipeline/scripts/bootstrap.py doctor
```

Then run **Actions → SF Pipeline Doctor**. It logs in to every org from GitHub with exactly the
pipeline's secrets and reports user, org ID and remaining API calls. It also runs every Monday, so an
expired login surfaces before a deploy hits it.

## Trying it with scratch orgs

No sandboxes yet? With a Dev Hub:

```bash
python sf-pipeline/scripts/bootstrap.py scratch-orgs --dev-hub my-hub   # one scratch org per env, aliased to match
python sf-pipeline/scripts/bootstrap.py setup --pat-from-gh            # GitHub + secrets + doctor
```

Scratch orgs expire after 30 days (`--days`). The pipeline passes `--ignore-conflicts` to deploys, so
source-tracked orgs (scratch orgs and tracked sandboxes) behave like any other org.

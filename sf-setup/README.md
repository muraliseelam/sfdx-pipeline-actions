# sf-setup — org bootstrap kit (low-touch org onboarding)

This folder lives in the **central actions repo** and prepares each Salesforce
org for the pipeline with as little manual clicking as possible. It replaces most
of the old manual runbook (create Connected App, upload cert, create integration
user, copy Consumer Key, paste 4 secrets per org) with scripted, idempotent steps.

```
sf-setup/
├── metadata/                                  # deployable Salesforce source
│   └── force-app/main/default/
│       ├── connectedApps/SF_CICD_Deploy.connectedApp-meta.xml
│       └── permissionsets/SF_CICD_Deploy.permissionset-meta.xml
├── metadata-eca/                              # the same app as an External Client App
│   └── force-app/main/default/ext*/           # (orgs that refuse Connected Apps)
└── scripts/
    ├── gen-jwt-keypair.sh          # 1 keypair, reused for all orgs
    ├── bootstrap-org.sh            # ← main: prepare ONE org end-to-end
    ├── bootstrap-from-config.sh    # every org in pipeline.config.yml (+ config-orgs.py)
    ├── bootstrap-all.sh            # loop over orgs.tsv to do them all
    ├── create-integration-user.sh  # + apex/createIntegrationUser.apex
    ├── push-env-secrets.sh         # write the 4 JWT secrets into a GH Environment
    └── orgs.tsv.example            # table for bootstrap-all.sh
```

## What gets automated

| Old manual step | Now |
|---|---|
| Generate JWT key + cert | `gen-jwt-keypair.sh` (once) |
| Create Connected App in each org | **deployed as metadata** (cert injected); orgs that refuse new Connected Apps get it as an **External Client App** |
| Upload certificate | injected into the app before deploy |
| Copy the Consumer Key | **read back automatically** via retrieve |
| Create the integration user | anonymous Apex (`createIntegrationUser.apex`) |
| Grant the user deploy permissions | `SF_CICD_Deploy` permission set, auto-assigned |
| Paste `SF_*` secrets per environment | `push-env-secrets.sh` via `gh` |

## Prerequisites
- `sf` (Salesforce CLI), `openssl`, `python3`, and `gh` (authenticated: `gh auth status`)
- **System Administrator** login for each org (used once to deploy the app)
- Admin on the target GitHub repo (to set Environment secrets)

## One org at a time
```bash
cd sf-setup/scripts
./bootstrap-org.sh \
  --org-alias int-admin \
  --instance-url https://test.salesforce.com \
  --repo your-org/sfdx-pipeline-example --env int \
  --int-username cicd@acme.com.int \
  --contact-email devops@acme.com
```
On first run it opens a browser for a **one-time admin login** to that org, then
deploys the app + permission set, reads the Consumer Key, creates the integration
user, and writes `SF_CONSUMER_KEY / SF_USERNAME / SF_INSTANCE_URL / SF_JWT_KEY`
into the `int` environment.

## All orgs in one pass (config-driven — recommended)

The actual sandbox names live only in your project's
`sf-pipeline/pipeline.config.yml` (`org.prod_org` — one variable; sandboxes default to the stage names).
Run this from the **project repo root** so it reads that config:

```bash
# from the project repo root (sfdx-pipeline-example), pointing at your central checkout
/path/to/central/sf-setup/scripts/bootstrap-from-config.sh \
  --repo your-org/sfdx-pipeline-example \
  --contact-email devops@acme.com
```

It derives every org (login URL + integration username) from the single
`sandbox:` value per environment — including `prod` and `prod-validate` — and
bootstraps each. Nothing is duplicated; change a sandbox name in one place.

## All orgs in one pass (static table — alternative)
If you'd rather not derive from config, use a table instead:
```bash
cd sf-setup/scripts
cp orgs.tsv.example orgs.tsv    # edit rows: env  alias  instance-url  username
./bootstrap-all.sh --repo your-org/sfdx-pipeline-example --contact-email devops@acme.com
```

## Orgs that refuse new Connected Apps
Orgs created since Spring '26 reject a Connected App deploy with *You can't create a
connected app*. `bootstrap-org.sh` then deploys the same `SF_CICD_Deploy` app as an
**External Client App** (`metadata-eca/`): same certificate, pre-authorized for
`--profile`, with a generated consumer key kept in `--key-dir` so re-runs reuse it.
The pipeline's JWT login works the same with either app. Force one with
`--app-type connected|external` (default `auto`).

## The ONE residual manual action (only if needed)
The platform requires an authenticated admin session to deploy the first app —
that's the one-time browser login above. Separately, if a later JWT login is
refused, pre-authorize the app once:

> Setup → **App Manager** → **SF CICD Deploy** → **Manage** → **Edit Policies** →
> Permitted Users = *Admin approved users are pre-authorized* → add the
> integration user's **profile** or the `SF_CICD_Deploy` permission set → Save.

The metadata already sets `permittedUsers=adminApproved`, so in most orgs the
System Administrator profile authenticates without this extra click.

## Security notes
- `.secrets/` (keys) is created locally and must **never** be committed. Add
  `.secrets/` to the consuming repo's `.gitignore`.
- `SF_JWT_KEY` is stored base64-encoded so PEM newlines survive GitHub secrets.
- Reuse the same keypair across orgs, or pass `--key-dir` to isolate per org.
- `push-env-secrets.sh` only creates an environment that is missing. It never
  re-writes an existing one, so bootstrapping `prod` again keeps its required
  reviewers and its deployment branch rule.

## Running it from CI (optional, fully hands-off)
See `.github/workflows/org-bootstrap.yml` in this repo — a reusable workflow that
runs `bootstrap-org.sh` with `--login jwt` using an admin `SFDX_AUTH_URL` secret,
so onboarding a new org is a single `workflow_dispatch`
(`examples/consumer/.github/workflows/org-bootstrap.yml` is the caller).

It checks this kit out of the central repo at the ref the caller pinned, and needs
two repository secrets in the consuming repo:

| Secret | Value |
|---|---|
| `SFDX_AUTH_URL` | `sf org display --verbose --target-org <alias>` → *Sfdx Auth Url*, for an admin of the org being bootstrapped. One org per value: replace it before the next run |
| `BOOTSTRAP_TOKEN` | A PAT with admin on the consuming repo (writes Environment secrets; also reads this repo if your copy is private) |

Any workflow a writer pushes can read repository secrets, so **delete both** once
the last org is done.

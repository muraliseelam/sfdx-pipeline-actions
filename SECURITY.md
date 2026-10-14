# Security

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security → Report a vulnerability) rather
than a public issue. Include steps to reproduce and the impact. We aim to respond within a week.

## How the pipeline handles credentials

- **Org credentials** are GitHub secrets that only the pipeline's jobs read.
  - `bootstrap.py` copies them from your local Salesforce CLI and never prints them.
  - JWT keys are generated outside the repository (`~/.sf-pipeline/`); `.gitignore` blocks `*.key`
    and `*.pem`.
- **Production credentials** live in the `production` and `production-validate` GitHub
  Environments, which only protected branches can use. No pull request or feature-branch workflow
  can read them.
- **Pull requests can't change the rules they're checked against.** The config, scripts and
  composite actions always come from the default branch; a PR only supplies Salesforce metadata.
- **The pipeline token** opens PRs and enables auto-merge. A separate admin-capable token
  (`github.admin_pat_secret`) can be confined to `production-validate` for the env-branch sync.

## Trust model

Branch protection, approvals and the release gates prevent *mistakes*: unreviewed merges, untested
Apex, deploying the wrong artifact, merging a release that never reached production. They don't stop
a malicious collaborator with write access, who can change workflow files on a branch. Grant write
access accordingly, and add required reviewers to the `production` environment
(`bootstrap.py github --reviewer <login>`) when several people can approve releases.

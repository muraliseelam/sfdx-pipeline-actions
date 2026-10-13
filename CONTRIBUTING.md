# Contributing

Thanks for considering a contribution.

This project is a **central GitHub Actions pipeline for Salesforce delivery**.
It is consumed by other repositories, which means almost every change here is a
change to somebody else's production deployment path. That shapes how we work.

## The one rule that matters

**A reusable workflow in this repository runs inside the *consumer's* checkout.**

`$GITHUB_WORKSPACE` is the calling project, not this repository. So a reusable
workflow can never run `sf-pipeline/scripts/…` directly — that path does not
exist at runtime. It must call a **composite action** in this repo, and that
action reaches its sibling scripts through `$GITHUB_ACTION_PATH`:

```yaml
# .github/workflows/deploy.yml  (reusable — runs in the consumer's checkout)
- uses: muraliseelam/sfdx-pipeline-actions/sf-pipeline/deploy@main   # ✅

- run: bash sf-pipeline/scripts/delta.sh                         # ❌ not there
```

```yaml
# sf-pipeline/deploy/action.yml  (composite — knows where it lives)
- run: bash "$GITHUB_ACTION_PATH/../scripts/delta.sh"            # ✅
```

Breaking this is the single most common mistake, and it fails only at runtime,
in someone else's repository.

## Getting set up

```bash
git clone https://github.com/muraliseelam/sfdx-pipeline-actions
cd sfdx-pipeline-actions
./scripts/set-repo-owner.sh your-fork/sfdx-pipeline-actions   # repoint internal refs
```

You will need `bash`, `python3`, `node`, and the
[Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli) for
anything that touches an org.

## Before you open a pull request

```bash
./scripts/validate.sh        # YAML parse, shell syntax, python compile, ref integrity
```

CI runs the same checks. Please also:

- Keep behaviour driven by `pipeline.config.yml`. Project-specific values do not
  belong in workflow or action files.
- Update `docs/Product_Documentation.md` when you change behaviour, and
  `docs/Pipeline_User_Guide.md` when you change what a user has to *do*.
- Use `@main` for internal references on `main`. Release tooling rewrites them
  at tag time — see below.

## Testing a change for real

There is no mock Salesforce. A meaningful change should be exercised end to end
against real sandboxes:

1. Push your branch to a fork.
2. In a test consumer repository, point one caller at your branch
   (`@your-branch`) and open a pull request.
3. Because `pull_request` runs the merge commit's workflows, that pull request
   validates your change without merging anything.
4. Report what you ran in your pull request description.

## Releasing (maintainers)

> [!IMPORTANT]
> **Never tag `main` directly.**

The reusable workflows reference this repository's own composite actions dozens
of times, and on `main` every one of those reads `@main`. `uses:` does not
accept expressions, so a tag cut straight from `main` would freeze the
entry-point workflow while every action it invokes still resolved to the moving
branch — a consumer pinning `@v1` would be pinned in name only.

Each release tag therefore carries a commit, off `main`, that rewrites every
internal reference to point at that tag:

```bash
./scripts/cut-release.sh v1.2.0     # creates the ref-rewrite commit and tags it
./scripts/verify-tag.sh v1.2.0      # proves no @main survives in the tag
```

`main` keeps `@main`. Only tags are self-referential.

Publishing a fork or a fresh copy under a different owner has extra steps —
see [PUBLISHING.md](PUBLISHING.md).

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Contributions are accepted under the [MIT Licence](LICENSE).

# Publishing this pipeline under your own account

You are about to put this repository somewhere public under your own name. There
are only four steps, but **two of them are easy to skip and both failures are
silent**, so the order below matters.

Everything here runs offline except the final push.

---

## Before you start

Create the empty GitHub repository you want this to live in. You need its full
slug — `owner/repo`, for example `jane/salesforce-pipeline`. Nothing else.

You do **not** need a Salesforce org to publish. Standing up orgs happens later,
per consuming project, and is covered in
[the setup guide](./docs/Pipeline_User_Guide.md#part-1--set-up-the-pipeline-one-time).

---

## Step 1 — Make the repository yours

```bash
./scripts/personalize.sh <owner>/<repo> "<copyright holder>" <contact email>

# for example:
./scripts/personalize.sh jane/salesforce-pipeline "Jane Doe" jane@example.com
```

That one command does all of this:

| What | Why it matters |
|---|---|
| Repoints every `uses:` at your slug | The reusable workflows call this repository's **own** composite actions by full name. Left unchanged, your workflows silently execute the upstream project's code instead of yours |
| Fills the LICENSE copyright holder | An MIT licence with an unfilled holder is not a licence anyone can rely on |
| Fills the security and conduct contacts | Otherwise a reporter has nowhere to send a vulnerability |

It refuses to finish while any placeholder remains, so you cannot half-do it.

### It also sets your commit identity

The third thing the script does is set `user.name` and `user.email` **for this
repository only**.

This matters more than it sounds. Git has no per-repository identity by default
— it falls back to your machine-wide one. If that machine-wide identity is a
work address, then every commit in your new public repository is authored by
your employer's email, permanently and visibly, and no amount of fixing the
LICENSE afterwards undoes it.

If you copy these files into a freshly `git init`-ed folder, that local setting
does not come with them. Either copy the `.git` directory too, or re-run
`personalize.sh` in the new folder, or set it by hand:

```bash
git config user.name  "Your Name"
git config user.email you@example.com
```

Check before your first commit:

```bash
git config user.email      # must NOT be your work address
```

### A note on `gh` authentication

`git push` over HTTPS uses whatever account `gh auth status` reports. If that is
a work or enterprise account, the push is attributed to it regardless of your
commit identity. Verify with `gh auth status`, and use `gh auth switch` or an
SSH remote if it is not the account you intend to publish under.

> **Why not just find-and-replace?** The slug appears in workflows, composite
> actions, shell scripts, Markdown and the `.drawio` diagram sources. The script
> discovers the current value rather than assuming it, so it still works if you
> run it twice or have already renamed once.

---

## Step 2 — Confirm nothing broke

```bash
./scripts/validate.sh
```

Thirteen offline checks, including that every internal reference resolves to a
real path, that the example consumer matches the example config, that the
environment-name portability suite passes, and that branch protection leaves no
pipeline branch open. All thirteen must pass before you push.

---

## Step 3 — Push `main`

```bash
git add -A
git commit -m "Initial release"
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```

---

## Step 4 — Cut the `v1` tags  ← **do not skip this**

```bash
./scripts/cut-release.sh v1.0.0 --push    # the immutable release
./scripts/cut-release.sh v1     --push    # the moving major alias
```

**The example consumer callers pin `@v1`.** Until that tag exists, every consumer
workflow fails to resolve and the pipeline appears completely dead — with an
error that points at the consumer, not at the missing tag. This is the single
most likely thing to go wrong on a fresh publish.

### Why tagging `main` directly would produce a broken release

GitHub does not allow expressions in `uses:`, so a reference cannot say "whatever
ref I was called as". On `main` every internal reference reads `@main`. Tag that
commit as-is and you get a release whose entry-point workflow is frozen at the
tag while every action it calls still floats on `main` — a consumer pinning `@v1`
would be pinned in name only.

So `cut-release.sh` puts one extra commit on each tag, off `main`, rewriting all
51 internal references to point at the tag itself. `main` keeps `@main` so
day-to-day development is never frozen. That commit is never merged back.

Verify any tag at any time:

```bash
./scripts/verify-tag.sh v1
```

---

## Afterwards

| Task | When |
|---|---|
| `./scripts/cut-release.sh v1.1.0 --push` then `v1 --push` | Every time you ship a change consumers should get |
| `./scripts/cut-release.sh v2 --push` | Only for a breaking change — consumers opt in by editing one character |
| Re-render `docs/diagrams/*.drawio` to PNG | If you changed the diagrams |

Point your first Salesforce project at it using
[User Guide, Part 1](./docs/Pipeline_User_Guide.md#part-1--set-up-the-pipeline-one-time).

---

## Checklist

- [ ] `personalize.sh` run, no placeholders left
- [ ] `validate.sh` — every check passes ("All checks passed.")
- [ ] `main` pushed
- [ ] `v1.0.0` **and** `v1` tags pushed
- [ ] `verify-tag.sh v1` passes
- [ ] A consumer repo can resolve `<owner>/<repo>/.github/workflows/pr-validation.yml@v1`

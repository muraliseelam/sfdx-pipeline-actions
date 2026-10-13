## What this changes

<!-- One or two sentences. What behaviour is different after this merges? -->

## Why

<!-- The problem being solved. Link an issue if there is one. -->

## Impact on consumers

<!-- Tick one. Remember: changes here land in other people's deploy path. -->

- [ ] No behaviour change (docs, refactor, tests)
- [ ] Additive and opt-in — existing projects are unaffected
- [ ] **Behaviour change** — existing projects will act differently (explain below)

## How this was tested

<!--
There is no mock Salesforce. For anything beyond a docs change, say what you
actually ran. The usual approach is to point a test consumer's caller at your
branch and open a pull request — `pull_request` runs the merge commit's
workflows, so that validates the change without merging it.
-->

- [ ] `./scripts/validate.sh` passes
- [ ] Exercised against a real org (describe below)

## Checklist

- [ ] Behaviour stays driven by `pipeline.config.yml` — no project-specific
      values hard-coded into workflows or actions
- [ ] Any new script is called through a composite action using
      `$GITHUB_ACTION_PATH`, not directly from a reusable workflow
- [ ] Internal references still use `@main`
- [ ] `docs/Product_Documentation.md` updated if behaviour changed
- [ ] `docs/Pipeline_User_Guide.md` updated if the user-facing steps changed

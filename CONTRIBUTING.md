# Contributing

Thanks for helping. The pipeline is plain GitHub Actions YAML plus small, dependency-light Python
scripts, so it's easy to work on locally.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r sf-pipeline/requirements-dev.txt   # PyYAML, actionlint, shellcheck
```

## Before opening a PR

```bash
python sf-pipeline/scripts/pipeline_config.py validate
python -m unittest discover -s sf-pipeline/tests -v
python sf-pipeline/scripts/lint.py                # actionlint + shellcheck (incl. composite actions)
```

The **Pipeline Self-Test** workflow runs the same three steps on every PR that touches pipeline files.

## Guidelines

- **Nothing environment-specific outside `pipeline.config.yml`.** New settings get a default in
  `pipeline_config.DEFAULTS`, validation in `validate()`, a flat key in `globals_view`/`env_view`
  when workflows need it, and a row in `docs/configuration.md`.
- **Workflows stay thin.** Logic that can be unit-tested belongs in `sf-pipeline/scripts/`.
- **Salesforce CLI behaviour is verified against a real org.** Parsers are tested with real
  `--json` output in `sf-pipeline/tests/fixtures/` (trimmed; no IDs or usernames).
- **Fail loudly with a fix.** Every error should name what's missing and the command that fixes it.
- **Run bash under GitHub's `-eo pipefail`.** Scripts must behave correctly with it.

## Changing pipeline files on a live repo

Pipeline files normally reach `main` through a release. If you change them directly on `main` (as a
maintainer), run **Sync Env Branches** afterwards, so open PRs into env branches don't conflict with
older copies.

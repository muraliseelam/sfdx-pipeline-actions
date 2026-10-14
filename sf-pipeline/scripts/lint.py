#!/usr/bin/env python3
"""Lint the pipeline's own GitHub Actions code.

  1. actionlint (with shellcheck) on every workflow in .github/workflows
  2. shellcheck on every `run:` block of the composite actions in .github/actions
     (actionlint doesn't look inside composite actions)

Install the linters with:  pip install -r sf-pipeline/requirements-dev.txt
Exit code is non-zero when anything is reported.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        sys.exit(f"{tool} not found; run: pip install -r sf-pipeline/requirements-dev.txt")
    return path


def main() -> int:
    actionlint, shellcheck = need("actionlint"), need("shellcheck")
    failures = 0

    workflows = sorted(str(p) for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    proc = subprocess.run([actionlint, "-shellcheck", shellcheck, *workflows], cwd=ROOT, capture_output=True, text=True)
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode:
        failures += 1
    print(f"actionlint: {len(workflows)} workflow(s), {'issues found' if proc.returncode else 'clean'}")

    blocks = 0
    for action in sorted((ROOT / ".github" / "actions").glob("*/action.yml")):
        for step in yaml.safe_load(action.read_text(encoding="utf-8"))["runs"]["steps"]:
            run = step.get("run")
            if not run:
                continue
            blocks += 1
            # Replace ${{ ... }} the way actionlint does before handing the script to shellcheck.
            script = "#!/usr/bin/env bash\nset -eo pipefail\n" + re.sub(r"\$\{\{.*?\}\}", "EXPR", run, flags=re.S)
            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8", newline="\n") as tmp:
                tmp.write(script)
            result = subprocess.run([shellcheck, "-s", "bash", "-S", "style", tmp.name], capture_output=True, text=True)
            Path(tmp.name).unlink()
            if result.returncode:
                failures += 1
                rel = action.relative_to(ROOT).as_posix()
                print(f"{rel} :: {step.get('name')}\n{result.stdout}")
    print(f"shellcheck: {blocks} composite run block(s), {'issues found' if failures else 'clean'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

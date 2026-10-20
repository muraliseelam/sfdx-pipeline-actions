#!/usr/bin/env python3
"""audit.py — deployment audit trail (REQ-018).

Produces a single structured audit record for one pipeline action (validate,
deploy, promote, rollback) and renders a human-readable block into the job
summary. The record is written to `.deploy/audit-record.json` (picked up as a
build artifact) and printed as one compact JSON line on stdout so a caller can
append it to the durable `audit-log` branch.

Everything is sourced from env vars + the artifacts the deploy step already
creates (`.deploy/report.json`, `.deploy/coverage.json`, `.sgd/package/package.xml`),
so there is nothing extra to maintain.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def component_summary(pkg_path):
    """Return (total, {type: count}) from a package.xml."""
    counts = {}
    total = 0
    try:
        xml = open(pkg_path, encoding="utf-8").read()
    except Exception:
        return total, counts
    for block in re.findall(r"<types>(.*?)</types>", xml, re.S):
        name = re.search(r"<name>(.*?)</name>", block, re.S)
        members = re.findall(r"<members>(.*?)</members>", block, re.S)
        if name and members:
            counts[name.group(1).strip()] = len(members)
            total += len(members)
    return total, counts


def env(name, default=""):
    return os.environ.get(name, default) or default


def main():
    coverage = read_json(".deploy/coverage.json") or {}
    total, counts = component_summary(".sgd/package/package.xml")

    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": env("AUDIT_TYPE", "Deploy"),
        "environment": env("AUDIT_ENVIRONMENT"),
        "status": env("AUDIT_STATUS", "unknown"),
        "check_only": env("AUDIT_CHECK_ONLY", "false") == "true",
        "actor": env("AUDIT_ACTOR"),
        "ref": env("AUDIT_REF"),
        "sha": env("AUDIT_SHA"),
        "short_sha": env("AUDIT_SHA")[:8],
        "pr": env("AUDIT_PR"),
        "deploy_id": env("AUDIT_DEPLOY_ID"),
        # The check-only validation behind this run (for a quick deploy, the
        # validated job it promoted). Blank for a plain deploy.
        "validation_id": env("AUDIT_VALIDATION_ID"),
        "run_id": env("AUDIT_RUN_ID"),
        "run_url": env("AUDIT_RUN_URL"),
        "repo": env("AUDIT_REPO"),
        "profiles_mode": env("AUDIT_PROFILES_MODE"),
        "components_total": total,
        "components_by_type": counts,
        "coverage_percent": coverage.get("percent"),
        "coverage_threshold": coverage.get("threshold"),
    }

    os.makedirs(".deploy", exist_ok=True)
    with open(".deploy/audit-record.json", "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)

    # One compact line for the durable append step.
    print(json.dumps(record, separators=(",", ":")))

    icon = {"success": "✅", "no_changes": "⚪", "failure": "❌"}.get(record["status"], "•")
    mode = "check-only" if record["check_only"] else "deploy"
    comp = ", ".join(f"{k} × {v}" for k, v in sorted(counts.items())) or "—"
    cov = ("n/a" if record["coverage_percent"] is None
           else f"{record['coverage_percent']}% (min {record['coverage_threshold']}%)")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"\n### 🧾 Audit — {record['type']} → {record['environment']} {icon}\n\n"
                "| Field | Value |\n|---|---|\n"
                f"| Status | **{record['status']}** ({mode}) |\n"
                f"| Actor | {record['actor'] or '—'} |\n"
                f"| Commit | `{record['short_sha']}`"
                + (f" (PR #{record['pr']})" if record['pr'] else "") + " |\n"
                f"| Components | {record['components_total']} — {comp} |\n"
                f"| Profiles mode | {record['profiles_mode'] or '—'} |\n"
                f"| Apex coverage | {cov} |\n"
                f"| Deploy id | `{record['deploy_id'] or '—'}` |\n"
                + (f"| Validation id | `{record['validation_id']}` |\n"
                   if record['validation_id'] and record['validation_id'] != record['deploy_id'] else "")
                + f"| Run | {record['run_url'] or '—'} |\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

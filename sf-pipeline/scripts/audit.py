#!/usr/bin/env python3
"""audit.py — deployment audit trail (REQ-018).

Produces a single structured audit record for one pipeline action (validate,
deploy, promote, rollback) and renders a human-readable block into the job
summary. The record is written to `.deploy/audit-record.json` (picked up as a
build artifact) and printed as one compact JSON line on stdout so a caller can
append it to the durable `audit-log` branch.

Everything is sourced from AUDIT_* env vars set by the audit action from the
deploy action's outputs, plus the delta manifest (AUDIT_PACKAGE, default
`.sgd/package/package.xml`), so there is nothing extra to maintain.
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
    total, counts = component_summary(env("AUDIT_PACKAGE", ".sgd/package/package.xml"))
    pct = env("AUDIT_COVERAGE")

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
        "run_id": env("AUDIT_RUN_ID"),
        "run_url": env("AUDIT_RUN_URL"),
        "repo": env("AUDIT_REPO"),
        "profiles_mode": env("AUDIT_PROFILES_MODE"),
        "test_level": env("AUDIT_TEST_LEVEL"),
        "components_total": total,
        "components_by_type": counts,
        "coverage_percent": float(pct) if pct else None,
        "coverage_threshold": env("AUDIT_THRESHOLD") or None,
    }

    out_dir = env("AUDIT_DIR", ".deploy")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "audit-record.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)

    # One compact line for the durable append step.
    print(json.dumps(record, separators=(",", ":")))

    icon = {"success": "✅", "skipped": "⚪", "no_changes": "⚪", "failure": "❌", "failed": "❌"}.get(record["status"], "•")
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
                f"| Run | {record['run_url'] or '—'} |\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

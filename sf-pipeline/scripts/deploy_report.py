#!/usr/bin/env python3
"""deploy_report.py — turn a Salesforce `sf project deploy` --json result into a
human-readable Markdown summary and a compact status JSON.

The goal is that a reviewer (including non-technical folks) can read *what failed*
directly on the pull request, instead of digging through Actions logs.

It is deliberately defensive about the shape of the CLI output: on success the
deploy details live under `result`, but when the command exits non-zero the sf CLI
wraps everything in an error envelope where the details live under `data` (and
`result` may be empty). We therefore search recursively for the interesting keys.

Usage:
  python3 scripts/ci/deploy_report.py \
      --result deploy-result.json \
      --status failure \
      --environment int \
      --check-only true \
      --out-md .deploy/errors.md \
      --out-json .deploy/report.json
"""
import argparse
import json
import os
import sys


def load(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return {}


def find_all(obj, key):
    """Recursively collect every value stored under `key` anywhere in `obj`."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(find_all(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_all(item, key))
    return found


def as_list(value):
    """SF sometimes returns a single object where a list is expected."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def collect(obj, key):
    """Flatten every list/obj found under `key` into a single list."""
    out = []
    for v in find_all(obj, key):
        out.extend(as_list(v))
    return out


def first_str(obj, *keys):
    for k in keys:
        vals = find_all(obj, k)
        for v in vals:
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def esc(text):
    """Make a value safe for a Markdown table cell."""
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="deploy-result.json")
    ap.add_argument("--status", required=True,
                    help="success | failure | no_changes")
    ap.add_argument("--environment", default="")
    ap.add_argument("--check-only", default="false")
    ap.add_argument("--coverage-file", default=".deploy/coverage.json")
    ap.add_argument("--out-md", default=".deploy/errors.md")
    ap.add_argument("--out-json", default=".deploy/report.json")
    args = ap.parse_args()

    data = load(args.result)
    check_only = str(args.check_only).lower() == "true"
    verb = "Validation" if check_only else "Deployment"

    # --- Apex coverage (written by the deploy action's coverage gate) --------
    cov = load(args.coverage_file)
    cov_pct = cov.get("percent")
    cov_threshold = cov.get("threshold")
    cov_measured = bool(cov.get("measured"))
    cov_per_class = cov.get("per_class") or []
    cov_failed = bool(
        cov_measured
        and cov_pct is not None
        and cov_threshold is not None
        and float(cov_pct) + 1e-9 < float(cov_threshold)
    )

    deploy_id = first_str(data, "id")

    # --- Component (compile / metadata) failures ----------------------------
    comp_failures = collect(data, "componentFailures")
    comp_rows = []
    for c in comp_failures:
        if not isinstance(c, dict):
            continue
        # `success: true` entries can appear in the same list; keep only failures.
        if c.get("success") in (True, "true"):
            continue
        comp_rows.append({
            "component": c.get("fullName") or c.get("name") or "",
            "type": c.get("componentType") or c.get("type") or "",
            "line": c.get("lineNumber") or "",
            "problem": c.get("problem") or c.get("problemType") or "",
        })

    # --- Apex test failures --------------------------------------------------
    run_tests = {}
    rt = find_all(data, "runTestResult")
    if rt and isinstance(rt[0], dict):
        run_tests = rt[0]
    test_failures = []
    for f in as_list(run_tests.get("failures")) or collect(data, "failures"):
        if not isinstance(f, dict):
            continue
        # A component failure list also uses "failures"; keep only test-shaped ones.
        if "methodName" not in f and "method" not in f:
            continue
        test_failures.append({
            "class": f.get("name") or f.get("apexClass") or "",
            "method": f.get("methodName") or f.get("method") or "",
            "message": f.get("message") or "",
            "stack": f.get("stackTrace") or "",
        })

    # --- Coverage warnings ---------------------------------------------------
    cov_warnings = []
    for w in collect(data, "codeCoverageWarnings"):
        if isinstance(w, dict) and w.get("message"):
            cov_warnings.append(w["message"])

    top_message = first_str(data, "message", "problem")

    # ---- Build Markdown -----------------------------------------------------
    md = []
    if args.status == "failure":
        md.append(f"**❗ {verb} failed** for `{args.environment}`. "
                  "Here is what needs to be fixed before this can be merged:\n")
        if comp_rows:
            md.append(f"#### 🧩 Code / metadata problems ({len(comp_rows)})\n")
            md.append("| Component | Type | Line | Problem |")
            md.append("|---|---|---|---|")
            for r in comp_rows:
                md.append(f"| {esc(r['component'])} | {esc(r['type'])} | "
                          f"{esc(r['line'])} | {esc(r['problem'])} |")
            md.append("")
        if test_failures:
            md.append(f"#### 🧪 Failed tests ({len(test_failures)})\n")
            md.append("| Test class | Method | Why it failed |")
            md.append("|---|---|---|")
            for r in test_failures:
                msg = esc(r["message"]) or esc(r["stack"])
                md.append(f"| {esc(r['class'])} | {esc(r['method'])} | {msg} |")
            md.append("")
        if cov_warnings:
            md.append("#### 📉 Code coverage warnings\n")
            for m in cov_warnings:
                md.append(f"- {esc(m)}")
            md.append("")
        if cov_failed:
            md.append("#### 📊 Apex test coverage too low\n")
            md.append(f"- Aggregate coverage **{cov_pct}%** is below the required "
                      f"**{int(float(cov_threshold))}%**. Add tests for the changed "
                      "Apex before this can be deployed.\n")
            if cov_per_class:
                md.append("| Incoming Apex class | Coverage |")
                md.append("|---|:--:|")
                for r in cov_per_class:
                    pc = "n/a" if r.get("percent") is None else f"{r['percent']}%"
                    md.append(f"| {esc(r.get('name'))} | {pc} |")
                md.append("")
        if not comp_rows and not test_failures and not cov_warnings and not cov_failed:
            # Fall back to whatever message the CLI gave us.
            md.append("> " + (esc(top_message) or
                              "The deployment failed but no structured error "
                              "details were returned. Check the run logs.") + "\n")
    elif args.status == "no_changes":
        md.append("_No deployable Salesforce changes were detected against "
                  "the baseline — nothing to validate._\n")
    else:  # success
        detail = "validated against" if check_only else "deployed to"
        md.append(f"✅ Successfully {detail} `{args.environment}`.\n")

    md_text = "\n".join(md).rstrip() + "\n"

    # ---- Build JSON summary -------------------------------------------------
    summary = {
        "status": args.status,
        "environment": args.environment,
        "check_only": check_only,
        "deploy_id": deploy_id,
        "component_errors": len(comp_rows),
        "test_errors": len(test_failures),
        "coverage_warnings": len(cov_warnings),
        "coverage_percent": cov_pct,
        "coverage_threshold": cov_threshold,
        "coverage_measured": cov_measured,
        "coverage_pass": (cov_measured and not cov_failed),
        "coverage_per_class": cov_per_class,
        "message": top_message,
    }

    for path, content in ((args.out_md, md_text),
                          (args.out_json, json.dumps(summary, indent=2))):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    # Also echo to the job summary so non-PR runs (deploy-pipeline) show errors.
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(f"\n## {verb} — {args.environment}\n\n{md_text}\n")

    sys.stdout.write(md_text)


if __name__ == "__main__":
    main()

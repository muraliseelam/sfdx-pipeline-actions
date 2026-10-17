#!/usr/bin/env python3
"""Turn `sf project deploy ... --json` output into a Markdown pipeline report.

Renders the "🚀 Salesforce Pipeline Report": outcome, delta range, test level,
component failures, Apex test failures, a coverage table for every changed
class/trigger against the configured threshold, and Salesforce coverage
warnings. When the result carries no structured failures (auth error, timeout,
CLI error, fail-fast synthetic result), a top-level message explains why.

It also evaluates the coverage gate: each changed non-test class/trigger must
reach coverage_threshold. It writes these step outputs:
  status (success|failed|skipped), passed, deploy_success, coverage_ok,
  job_id, overall_coverage, marker

Usage
  deploy_report.py --env int --org int --mode validate --result result.json \
      [--plan plan.json] [--threshold 85] [--from SHA --to SHA --baseline main] \
      [--package package.xml --destructive destructiveChanges.xml] \
      [--coverage-dir DIR] [--no-changes] [--run-url URL] \
      --output report.md [--github-output]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from specified_tests import read_package  # noqa: E402

MAX_ROWS = 50
MAX_REPORT_CHARS = 60000  # GitHub comments cap at 65536
MODE_TITLES = {"validate": "Check-only validation", "deploy": "Deployment", "quick": "Quick deploy"}


# ------------------------------------------------------------------------ helpers


def as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def cell(text: Any, limit: int = 400) -> str:
    s = str(text if text is not None else "").replace("\r", "").strip()
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s.replace("|", "\\|").replace("\n", "<br>")


def truthy(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def short(sha: str | None) -> str:
    return (sha or "")[:7]


def marker_for(env: str, mode: str) -> str:
    kind = "validate" if mode == "validate" else "deploy"
    return f"<!-- sf-pipeline-report:{env}:{kind} -->"


def load_json(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return {"status": 1, "message": "Deploy output was not valid JSON: " + p.read_text(errors="replace")[:500]}
    return data if isinstance(data, dict) else None


# ----------------------------------------------------------------------- coverage


def coverage_from_result(run_tests: dict) -> dict[str, tuple[str, int, int]]:
    """{lower name: (name, total lines, covered lines)} from runTestResult.codeCoverage."""
    rows: dict[str, tuple[str, int, int]] = {}
    for c in as_list(run_tests.get("codeCoverage")):
        name = c.get("name")
        if not name:
            continue
        total = int(c.get("numLocations") or 0)
        uncovered = int(c.get("numLocationsNotCovered") or 0)
        rows[name.lower()] = (name, total, max(total - uncovered, 0))
    return rows


def coverage_from_summary(coverage_dir: str | None) -> dict[str, tuple[str, int, int]]:
    """Fallback: istanbul json-summary written by --coverage-formatters json-summary."""
    rows: dict[str, tuple[str, int, int]] = {}
    if not coverage_dir or not Path(coverage_dir).is_dir():
        return rows
    for f in Path(coverage_dir).rglob("coverage-summary.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for key, val in data.items():
            if key == "total" or not isinstance(val, dict):
                continue
            lines = val.get("lines") or {}
            name = Path(key.replace("\\", "/")).name.split(".")[0]
            rows[name.lower()] = (name, int(lines.get("total") or 0), int(lines.get("covered") or 0))
    return rows


def pct(total: int, covered: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


# ------------------------------------------------------------------------- report


def build(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    plan = load_json(args.plan) or {}
    data = load_json(args.result)
    threshold = float(args.threshold)
    marker = marker_for(args.env, args.mode)
    title = MODE_TITLES.get(args.mode, args.mode)
    out: list[str] = [marker, "## 🚀 Salesforce Pipeline Report", ""]

    pkg_types = read_package(args.package) if args.package and Path(args.package).is_file() else {}
    destructive = read_package(args.destructive) if args.destructive and Path(args.destructive).is_file() else {}
    n_pkg = sum(len(v) for v in pkg_types.values())
    n_del = sum(len(v) for v in destructive.values())

    summary_rows: list[tuple[str, str]] = []

    def facts(outcome: str) -> None:
        summary_rows.insert(0, ("Result", outcome))
        out.extend(["| | |", "|---|---|"])
        out.extend(f"| **{k}** | {v} |" for k, v in summary_rows)
        out.append("")

    summary_rows.append(("Environment", f"`{args.env}` → org `{args.org}`"))
    summary_rows.append(("Mode", title))
    if args.to_sha:
        delta = f"`{short(args.from_sha)}..{short(args.to_sha)}`"
        if args.baseline:
            delta += f" (merge-base with `origin/{args.baseline}`)"
        summary_rows.append(("Delta", delta))
    if args.package:
        summary_rows.append(("Components", f"{n_pkg} to deploy, {n_del} to delete"))

    outputs = {
        "marker": marker,
        "job_id": "",
        "overall_coverage": "",
        "deploy_success": "false",
        "coverage_ok": "true",
        "passed": "false",
        "status": "failed",
    }

    # ---- nothing to deploy
    if args.no_changes:
        facts("⏭️ No deployable Salesforce metadata in this delta; nothing was sent to the org.")
        outputs.update(deploy_success="true", passed="true", status="skipped")
        return finish(out, args), outputs

    level = plan.get("test_level") or ("(validated job)" if args.mode == "quick" else "")
    tests = plan.get("tests") or []
    if level:
        summary_rows.append(("Test level", f"`{level}`" + (f" ({len(tests)} test classes)" if tests else "")))

    if data is None:
        facts(f"❌ {title} failed")
        out += ["> ❌ **No deploy result was produced.** The step failed before `sf` returned JSON; see the workflow logs.", ""]
        return finish(out, args), outputs

    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    if not result and isinstance(data.get("data"), dict):
        result = data["data"]
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    run_tests = details.get("runTestResult") if isinstance(details.get("runTestResult"), dict) else {}
    job_id = str(result.get("id") or "")
    outputs["job_id"] = job_id

    deploy_success = data.get("status") == 0 and (
        truthy(result.get("success")) or result.get("status") in ("Succeeded",)
    )
    outputs["deploy_success"] = "true" if deploy_success else "false"

    comp_failures = as_list(details.get("componentFailures"))
    test_failures = as_list(run_tests.get("failures"))
    cov_warnings = as_list(run_tests.get("codeCoverageWarnings"))
    tests_run = int(run_tests.get("numTestsRun") or result.get("numberTestsCompleted") or 0)
    tests_failed = int(run_tests.get("numFailures") or result.get("numberTestErrors") or 0)

    if result.get("numberComponentsTotal") is not None:
        summary_rows.append(
            (
                "Org result",
                f"{result.get('numberComponentsDeployed', 0)}/{result.get('numberComponentsTotal', 0)} components, "
                f"{result.get('numberComponentErrors', 0)} errors",
            )
        )
    if tests_run or tests_failed:
        summary_rows.append(("Apex tests", f"{tests_run} run, {tests_failed} failed"))

    # ---- coverage gate on changed Apex
    coverage = coverage_from_result(run_tests) or coverage_from_summary(args.coverage_dir)
    evaluated = bool(coverage) and level not in ("NoTestRun", "") and args.mode != "quick"
    changed = list(plan.get("apex_changed", []))
    cov_lines: list[str] = []
    below: list[str] = []
    if coverage:
        tot = sum(t for _, t, _ in coverage.values())
        cov = sum(c for _, _, c in coverage.values())
        outputs["overall_coverage"] = f"{pct(tot, cov):.1f}"
    if evaluated and changed:
        cov_lines += [f"### Code coverage: changed Apex (gate ≥ {threshold:g}%)", "",
                      "| Class / Trigger | Lines | Covered | Coverage | Gate |", "|---|---:|---:|---:|:---:|"]
        for item in changed:
            row = coverage.get(item["name"].lower())
            if row is None:
                if item.get("coverage_exempt"):
                    cov_lines.append(f"| `{cell(item['name'])}` | – | – | – | ➖ exempt (no executable lines) |")
                else:
                    # Not exercised by any test that ran: 0% as far as the gate is concerned.
                    below.append(f"{item['name']} (not covered)")
                    cov_lines.append(f"| `{cell(item['name'])}` | – | 0 | 0.0% | ❌ not covered by the tests run |")
                continue
            name, total, covered = row
            p = pct(total, covered)
            ok = p + 1e-9 >= threshold
            if not ok:
                below.append(f"{name} ({p:.1f}%)")
            cov_lines.append(f"| `{cell(name)}` | {total} | {covered} | {p:.1f}% | {'✅' if ok else '❌'} |")
        if outputs["overall_coverage"]:
            cov_lines += ["", f"Overall coverage of classes exercised by this run: **{outputs['overall_coverage']}%**"]
        cov_lines.append("")
    waived = bool(below) and bool(getattr(args, "bypass", ""))
    if below:
        outputs["coverage_ok"] = "false"
        gate = f"❌ below {threshold:g}%: {', '.join(below)}"
        if waived:
            gate = f"⚠️ below {threshold:g}%: {', '.join(below)}: **waived** ({cell(args.bypass)})"
    elif evaluated and changed:
        gate = f"✅ all changed Apex ≥ {threshold:g}%"
    elif args.mode == "quick":
        gate = "➖ enforced at validation"
    elif tests_run and not changed:
        gate = "➖ not evaluated (no changed Apex to gate)"
    else:
        gate = "➖ not evaluated (no Apex tests ran)"
    summary_rows.append(("Coverage gate", gate))
    if job_id:
        url = result.get("deployUrl") or ""
        summary_rows.append(("Job ID", f"[`{job_id}`]({url})" if url.startswith("https://") else f"`{job_id}`"))

    passed = deploy_success and (outputs["coverage_ok"] == "true" or waived)
    outputs["passed"] = "true" if passed else "false"
    outputs["status"] = "success" if passed else "failed"
    if passed:
        outcome = f"✅ {title} succeeded"
    elif deploy_success:
        outcome = f"❌ {title} succeeded in the org but failed the coverage gate"
    else:
        outcome = f"❌ {title} failed"
    facts(outcome)

    structured = bool(comp_failures or test_failures or cov_warnings)
    if not deploy_success and not structured:
        msg = cell(data.get("message") or result.get("errorMessage") or "Deployment failed; see the workflow logs.", 2000)
        name = data.get("name") or result.get("errorStatusCode") or ""
        out += [f"> ❌ **{cell(name)}**: {msg}" if name else f"> ❌ {msg}", ""]
    elif data.get("synthetic"):
        out += [f"> ❌ {cell(data.get('message'), 2000)}", ""]

    if comp_failures:
        out += [f"### Component failures ({len(comp_failures)})", "", "| Type | Name | Problem | Location |", "|---|---|---|---|"]
        for f in comp_failures[:MAX_ROWS]:
            loc = cell(f.get("fileName") or "")
            if f.get("lineNumber"):
                loc += f":{f.get('lineNumber')}" + (f":{f.get('columnNumber')}" if f.get("columnNumber") else "")
            out.append(
                f"| {cell(f.get('componentType'))} | `{cell(f.get('fullName'))}` | {cell(f.get('problem'))} | {loc} |"
            )
        if len(comp_failures) > MAX_ROWS:
            out.append(f"| … | {len(comp_failures) - MAX_ROWS} more | | |")
        out.append("")

    if test_failures:
        out += [f"### Apex test failures ({len(test_failures)})", "", "| Test | Method | Message |", "|---|---|---|"]
        for f in test_failures[:MAX_ROWS]:
            out.append(f"| `{cell(f.get('name'))}` | `{cell(f.get('methodName'))}` | {cell(f.get('message'))} |")
        if len(test_failures) > MAX_ROWS:
            out.append(f"| … | {len(test_failures) - MAX_ROWS} more | |")
        traces = [f for f in test_failures[:MAX_ROWS] if f.get("stackTrace")]
        if traces:
            out += ["", "<details><summary>Stack traces</summary>", ""]
            for f in traces:
                out += [f"**{f.get('name')}.{f.get('methodName')}**", "```", str(f.get("stackTrace"))[:1500], "```"]
            out += ["</details>"]
        out.append("")

    out += cov_lines

    if cov_warnings:
        out += ["### Salesforce coverage warnings", ""]
        out += [f"- `{cell(w.get('name') or 'org')}`: {cell(w.get('message'))}" for w in cov_warnings[:MAX_ROWS]]
        out.append("")

    untested = plan.get("untested") or []
    if untested:
        out += ["### Changed Apex without a test class", ""]
        out += [f"- `{u['name']}` ({u['type']}): looked for {', '.join(f'`{s}`' for s in u['searched'])}" for u in untested]
        out.append("")

    if tests:
        out += ["<details><summary>Test classes run</summary>", "", ", ".join(f"`{t}`" for t in tests), "", "</details>", ""]

    if pkg_types or destructive:
        out += ["<details><summary>Delta manifest</summary>", ""]
        for label, types in (("Deploy", pkg_types), ("Delete (post-destructive)", destructive)):
            if not types:
                continue
            out += [f"**{label}**", ""]
            for mtype in sorted(types):
                members = sorted(types[mtype], key=str.lower)
                shown = ", ".join(f"`{m}`" for m in members[:MAX_ROWS])
                more = f" … +{len(members) - MAX_ROWS}" if len(members) > MAX_ROWS else ""
                out.append(f"- **{mtype}** ({len(members)}): {shown}{more}")
            out.append("")
        out += ["</details>", ""]

    return finish(out, args), outputs


_FRAME = re.compile(r"(Class|Trigger)\.(?:(\w+)__)?(\w+)(?:\.[\w.]+)?: line (\d+), column (\d+)")


def _find_source(source_path: str, rel_name: str) -> str | None:
    """Repo-relative path of a metadata file such as 'classes/Foo.cls' under source_path."""
    root = Path(source_path)
    name = Path(rel_name.replace("\\", "/")).name
    suffix = "/" + rel_name.replace("\\", "/").lstrip("/")
    matches = [p for p in root.rglob(name) if p.is_file()]
    exact = [p for p in matches if p.as_posix().endswith(suffix)]
    pick = (exact or matches or [None])[0]
    return pick.as_posix() if pick else None


def annotations(data: dict | None, source_path: str) -> list[str]:
    """::error annotations that point at the failing source lines in the PR diff."""
    if not data:
        return []
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    run_tests = details.get("runTestResult") if isinstance(details.get("runTestResult"), dict) else {}
    out: list[str] = []

    def esc(text: Any) -> str:
        return str(text or "").replace("%", "%25").replace("\r", "").replace("\n", "%0A")

    def esc_prop(text: Any) -> str:
        # Property values (file=, title=) must also escape the separators ',' and ':'.
        return esc(text).replace(":", "%3A").replace(",", "%2C")

    for f in as_list(details.get("componentFailures")):
        path = _find_source(source_path, f.get("fileName") or "") if f.get("fileName") else None
        title = esc_prop(f"{f.get('componentType')} {f.get('fullName')}")
        loc = f"file={path}" if path else ""
        if path and f.get("lineNumber"):
            loc += f",line={f.get('lineNumber')}" + (f",col={f.get('columnNumber')}" if f.get("columnNumber") else "")
        out.append(f"::error {loc + ',' if loc else ''}title={title}::{esc(f.get('problem'))}")

    for f in as_list(run_tests.get("failures")):
        title = esc_prop(f"Apex test {f.get('name')}.{f.get('methodName')}")
        frame = _FRAME.search(f.get("stackTrace") or "")
        loc = ""
        if frame:
            kind, _ns, name, line, col = frame.groups()
            path = _find_source(source_path, f"{'classes' if kind == 'Class' else 'triggers'}/{name}.{'cls' if kind == 'Class' else 'trigger'}")
            if path:
                loc = f"file={path},line={line},col={col},"
        out.append(f"::error {loc}title={title}::{esc(f.get('message'))}")
    return out


def finish(lines: list[str], args: argparse.Namespace) -> str:
    footer = []
    if args.run_url:
        footer.append(f"[workflow run]({args.run_url})")
    if args.to_sha:
        footer.append(f"commit `{short(args.to_sha)}`")
    if footer:
        lines += ["---", "<sub>" + " · ".join(footer) + "</sub>"]
    text = "\n".join(lines).rstrip() + "\n"
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + "\n\n…report truncated; full result is in the workflow artifacts.\n"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", required=True)
    parser.add_argument("--org", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODE_TITLES))
    parser.add_argument("--result", help="sf --json output file")
    parser.add_argument("--plan", help="test plan JSON from specified_tests.py")
    parser.add_argument("--threshold", type=float, default=75.0)
    parser.add_argument("--from", dest="from_sha", default="")
    parser.add_argument("--to", dest="to_sha", default="")
    parser.add_argument("--baseline", default="")
    parser.add_argument("--package")
    parser.add_argument("--destructive")
    parser.add_argument("--coverage-dir")
    parser.add_argument("--no-changes", action="store_true")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output", action="store_true")
    parser.add_argument("--annotate", metavar="SOURCE_PATH", help="emit ::error annotations on failing source lines")
    parser.add_argument("--quiet", action="store_true", help="don't echo the report to stdout")
    parser.add_argument("--bypass", default="", help="admin bypass in effect (reason); waives the coverage gate")
    args = parser.parse_args(argv)

    text, outputs = build(args)
    Path(args.output).write_text(text, encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.quiet:
        print(text)
    if args.annotate:
        for line in annotations(load_json(args.result), args.annotate):
            print(line)
    if args.github_output:
        target = os.environ.get("GITHUB_OUTPUT")
        if not target:
            print("::error::--github-output used but GITHUB_OUTPUT is not set", file=sys.stderr)
            return 2
        with open(target, "a", encoding="utf-8") as fh:
            for k, v in outputs.items():
                fh.write(f"{k}={v}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

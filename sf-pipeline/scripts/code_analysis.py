#!/usr/bin/env python3
"""Turn `sf code-analyzer run --output-file results.json` into a PR report + gate.

Salesforce Code Analyzer (PMD, ESLint, RetireJS, Flow, regex, CPD engines)
reports violations with severity 1 (Critical) .. 5 (Info). Violations at or
above quality.code_analyzer.severity_threshold (default 2 = High) block the
PR; the rest are listed as advice. Engines that couldn't run (e.g. a missing
runtime) are reported as warnings, not violations.

Usage
  code_analysis.py --results results.json --env dev --threshold 2 --output analysis.md \
      [--annotate] [--github-output] [--run-url URL]
Outputs (with --github-output): passed, blocking, total, marker
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SEVERITY = {1: "Critical", 2: "High", 3: "Moderate", 4: "Low", 5: "Info"}
ICON = {1: "🛑", 2: "🔴", 3: "🟠", 4: "🟡", 5: "⚪"}
MAX_ROWS = 60


def cell(text: object, limit: int = 300) -> str:
    s = str(text or "").replace("\r", "").strip()
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s.replace("|", "\\|").replace("\n", " ")


def location(v: dict) -> dict:
    locs = v.get("locations") or [{}]
    idx = v.get("primaryLocationIndex") or 0
    return locs[idx] if 0 <= idx < len(locs) else locs[0]


def is_engine_error(v: dict) -> bool:
    return str(v.get("rule", "")).endswith("EngineError") or not location(v).get("file")


def rel(path: str) -> str:
    """Repo-relative POSIX path (the analyzer may report absolute or Windows paths)."""
    p = path.replace("\\", "/")
    cwd = os.getcwd().replace("\\", "/").rstrip("/") + "/"
    return p[len(cwd):] if p.startswith(cwd) else p


def analyze(data: dict, threshold: int) -> dict:
    violations = [v for v in data.get("violations") or [] if not is_engine_error(v)]
    engine_errors = [v for v in data.get("violations") or [] if is_engine_error(v)]
    blocking = [v for v in violations if int(v.get("severity", 5)) <= threshold]
    return {
        "violations": sorted(violations, key=lambda v: (int(v.get("severity", 5)), rel(location(v).get("file", "")),
                                                        int(location(v).get("startLine") or 0))),
        "engine_errors": engine_errors,
        "blocking": blocking,
        "counts": Counter(int(v.get("severity", 5)) for v in violations),
    }


def render(result: dict, env: str, threshold: int, run_url: str) -> str:
    marker = f"<!-- sf-pipeline-analysis:{env} -->"
    out = [marker, "## 🔍 Code Analysis (Salesforce Code Analyzer)", ""]
    blocking, violations = result["blocking"], result["violations"]
    counts = result["counts"]
    summary = " · ".join(f"{ICON[s]} {SEVERITY[s]} {counts[s]}" for s in sorted(SEVERITY) if counts.get(s))
    if blocking:
        out.append(f"❌ **{len(blocking)} blocking violation(s)** (severity {SEVERITY[threshold]} or worse) in the changed files.")
    elif violations:
        out.append(f"✅ No violations at {SEVERITY[threshold]} or worse; {len(violations)} lower-severity finding(s) below.")
    else:
        out.append("✅ No violations in the changed files.")
    if summary:
        out += ["", summary]
    out.append("")
    if violations:
        out += ["| Sev | Rule | Location | Message |", "|---|---|---|---|"]
        for v in violations[:MAX_ROWS]:
            sev = int(v.get("severity", 5))
            loc = location(v)
            where = f"`{cell(rel(loc.get('file', '')))}`" + (f":{loc.get('startLine')}" if loc.get("startLine") else "")
            rule = f"{v.get('engine')}:{v.get('rule')}"
            link = (v.get("resources") or [""])[0]
            rule_md = f"[`{cell(rule)}`]({link})" if link.startswith("https://") else f"`{cell(rule)}`"
            out.append(f"| {ICON.get(sev, '')} {SEVERITY.get(sev, sev)} | {rule_md} | {where} | {cell(v.get('message'))} |")
        if len(violations) > MAX_ROWS:
            out.append(f"| … | {len(violations) - MAX_ROWS} more | | |")
        out.append("")
    for e in result["engine_errors"]:
        first = str(e.get("message") or "").splitlines()[0] if e.get("message") else ""
        out.append(f"> ⚠️ Engine `{e.get('engine')}` didn't run: {cell(first, 400)}")
    if result["engine_errors"]:
        out.append("")
    out.append(f"<sub>Blocking threshold: {SEVERITY[threshold]} (quality.code_analyzer.severity_threshold)"
               + (f" · [workflow run]({run_url})" if run_url else "") + "</sub>")
    return "\n".join(out) + "\n"


def annotations(result: dict, threshold: int) -> list[str]:
    def esc(text: object) -> str:
        return str(text or "").replace("%", "%25").replace("\r", "").replace("\n", "%0A")

    def prop(text: object) -> str:
        return esc(text).replace(":", "%3A").replace(",", "%2C")

    lines = []
    # Low/Info findings stay in the report table only; annotating them would bury the diff.
    for v in [v for v in result["violations"] if int(v.get("severity", 5)) <= 3][:50]:
        sev = int(v.get("severity", 5))
        loc = location(v)
        level = "error" if sev <= threshold else "warning"
        props = [f"file={prop(rel(loc.get('file', '')))}"]
        if loc.get("startLine"):
            props.append(f"line={loc['startLine']}")
        if loc.get("startColumn"):
            props.append(f"col={loc['startColumn']}")
        title = f"{SEVERITY.get(sev, sev)} {v.get('rule')}"
        props.append(f"title={prop(title)}")
        lines.append(f"::{level} {','.join(props)}::{esc(v.get('message'))}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--threshold", type=int, default=2, choices=sorted(SEVERITY))
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--annotate", action="store_true")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.results)
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    result = analyze(data, args.threshold)
    text = render(result, args.env, args.threshold, args.run_url)
    Path(args.output).write_text(text, encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.annotate:
        for line in annotations(result, args.threshold):
            print(line)
    print(f"code analysis: {len(result['violations'])} violation(s), {len(result['blocking'])} blocking, "
          f"{len(result['engine_errors'])} engine error(s)")
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"passed={'false' if result['blocking'] else 'true'}\n")
            fh.write(f"blocking={len(result['blocking'])}\n")
            fh.write(f"total={len(result['violations'])}\n")
            fh.write(f"marker=<!-- sf-pipeline-analysis:{args.env} -->\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

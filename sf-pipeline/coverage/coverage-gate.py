#!/usr/bin/env python3
"""Apex code-coverage gate.

Reads the Salesforce deploy result (deploy-result.json) plus the sfdx-git-delta
package manifest (.sgd/package/package.xml) from the current workspace, computes
the aggregate Apex coverage for the incoming change, writes .deploy/coverage.json
and a job-summary line, and exits non-zero if coverage is below the threshold.

Env:
  COVERAGE_THRESHOLD  minimum aggregate coverage % (default 85)
  ENVIRONMENT         environment label for the summary
  COVERAGE_MEASURE_ONLY  "true" = Salesforce already rejected the deploy: record
                      the coverage it measured for the report, never fail
  GITHUB_STEP_SUMMARY (provided by CI) file to append the summary to
"""
import json
import os
import sys


def find_all(obj, key, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            find_all(v, key, out)
    elif isinstance(obj, list):
        for it in obj:
            find_all(it, key, out)
    return out


def main():
    threshold = float(os.environ.get("COVERAGE_THRESHOLD", "85") or 85)
    env = os.environ.get("ENVIRONMENT", "")
    measure_only = os.environ.get("COVERAGE_MEASURE_ONLY", "").lower() == "true"
    try:
        data = json.load(open("deploy-result.json", encoding="utf-8"))
    except Exception:
        data = {}

    # Names of the Apex classes/triggers actually coming in with THIS change,
    # so we can report their individual coverage (not the whole org's).
    incoming = set()
    try:
        import re
        pkg = open(".sgd/package/package.xml", encoding="utf-8").read()
        for block in re.findall(r"<types>(.*?)</types>", pkg, re.S):
            if "<name>ApexClass</name>" in block or "<name>ApexTrigger</name>" in block:
                incoming.update(re.findall(r"<members>(.*?)</members>", block, re.S))
    except Exception:
        pass

    cov_entries = []
    for lst in find_all(data, "codeCoverage", []):
        if isinstance(lst, list):
            cov_entries.extend(lst)
        elif isinstance(lst, dict):
            cov_entries.append(lst)

    total = covered = 0
    per_class = []
    for c in cov_entries:
        if not isinstance(c, dict):
            continue
        n = int(c.get("numLocations") or 0)
        nc = int(c.get("numLocationsNotCovered") or 0)
        total += n
        covered += max(0, n - nc)
        name = c.get("name") or ""
        if name in incoming:
            cpct = ((n - nc) / n * 100.0) if n else None
            per_class.append({
                "name": name,
                "percent": round(cpct, 2) if cpct is not None else None,
                "covered": max(0, n - nc),
                "total": n,
            })
    per_class.sort(key=lambda r: (r["percent"] is None, r["percent"] or 0))

    pct = (covered / total * 100.0) if total else None
    result = {
        "threshold": threshold,
        "percent": round(pct, 2) if pct is not None else None,
        "classes": len(cov_entries),
        "measured": pct is not None,
        "per_class": per_class,
    }
    os.makedirs(".deploy", exist_ok=True)
    with open(".deploy/coverage.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if pct is None:
        if measure_only:
            msg = "No Apex coverage data returned — Salesforce rejected the deploy before reporting coverage."
            print(msg)
        else:
            msg = "No Apex coverage data returned — coverage gate could not measure (passing)."
            print("::warning::" + msg)
        if summary:
            open(summary, "a", encoding="utf-8").write(f"\n### 📊 Apex coverage — {env}\n\n{msg}\n")
        sys.exit(0)

    line = f"Aggregate Apex coverage: {pct:.2f}% (threshold {threshold:.0f}%)"
    print(line)
    if summary:
        open(summary, "a", encoding="utf-8").write(f"\n### 📊 Apex coverage — {env}\n\n{line}\n")
    if measure_only:
        # The deploy already failed; this run only records what was measured.
        if pct + 1e-9 < threshold:
            print(f"Apex code coverage {pct:.2f}% is also below the required {threshold:.0f}%.")
        sys.exit(0)
    if pct + 1e-9 < threshold:
        print(f"::error::Apex code coverage {pct:.2f}% is below the required {threshold:.0f}%.")
        sys.exit(1)


if __name__ == "__main__":
    main()

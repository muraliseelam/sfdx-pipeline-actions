#!/usr/bin/env python3
"""Resolve the Apex test level and test classes for a delta package.xml.

Reads the package.xml produced by sfdx-git-delta, finds the ApexClass and
ApexTrigger members that changed, and maps each to companion test classes in
the source tree using the naming patterns / overrides in pipeline.config.yml.

Test level by the environment's test_policy:
  specified  RunSpecifiedTests with resolved companions; NoTestRun if no Apex changed
  local      RunLocalTests
  canary     RunSpecifiedTests with companions + the environment's canary_tests
  none       NoTestRun

When tests.require_companion is true, any changed non-test class/trigger without
a resolvable test class is reported as "untested". The deploy action then fails
fast; it never falls back to RunLocalTests or the whole-org suite. With
--synthetic-result a deploy-result JSON is written so the PR report renders the
failure like any other deploy error.

Usage
  specified_tests.py --package .sgd/package/package.xml --env int \
      [--out plan.json] [--synthetic-result result.json] [--github-output] [--annotate]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_config  # noqa: E402

APEX_TYPES = ("ApexClass", "ApexTrigger")
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_STRINGS = re.compile(r"'(?:\\.|[^'\\])*'")
_CLASS_KW = re.compile(r"\bclass\b", re.I)
_ISTEST = re.compile(r"@\s*istest\b", re.I)
_TYPE_KW = re.compile(r"\b(class|interface|enum)\b", re.I)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_package(path: str | Path) -> dict[str, list[str]]:
    """Return {metadata type: [members]} from a package.xml (namespace-agnostic)."""
    root = ET.parse(path).getroot()
    types: dict[str, list[str]] = {}
    for node in root:
        if _local(node.tag) != "types":
            continue
        name, members = None, []
        for child in node:
            tag = _local(child.tag)
            if tag == "name" and child.text:
                name = child.text.strip()
            elif tag == "members" and child.text:
                members.append(child.text.strip())
        if name:
            types.setdefault(name, []).extend(members)
    return types


def _code(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _STRINGS.sub("''", _COMMENTS.sub(" ", text))


def is_test_class(path: Path) -> bool:
    """True when @isTest annotates the top-level class declaration."""
    text = _code(path)
    match = _CLASS_KW.search(text)
    return bool(match and _ISTEST.search(text[: match.start()]))


def has_no_executable_code(path: Path) -> bool:
    """Top-level interfaces and enums have no coverable lines."""
    match = _TYPE_KW.search(_code(path))
    return bool(match and match.group(1).lower() in ("interface", "enum"))


@dataclass
class SourceIndex:
    """Apex classes/triggers in the source tree, keyed by lower-cased name."""

    classes: dict[str, Path] = field(default_factory=dict)
    triggers: dict[str, Path] = field(default_factory=dict)
    _test_cache: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def build(cls, source_path: str | Path) -> "SourceIndex":
        root = Path(source_path)
        index = cls()
        for p in root.rglob("*.cls"):
            index.classes[p.stem.lower()] = p
        for p in root.rglob("*.trigger"):
            index.triggers[p.stem.lower()] = p
        return index

    def class_name(self, name: str) -> str | None:
        p = self.classes.get(name.lower())
        return p.stem if p else None

    def is_test(self, name: str) -> bool:
        key = name.lower()
        if key not in self._test_cache:
            p = self.classes.get(key)
            self._test_cache[key] = bool(p and is_test_class(p))
        return self._test_cache[key]

    def no_executable_code(self, name: str) -> bool:
        p = self.classes.get(name.lower())
        return bool(p and has_no_executable_code(p))


def candidates(name: str, patterns: list[str]) -> list[str]:
    base = re.sub(r"(?i)trigger$", "", name) or name
    out = []
    for pat in patterns:
        cand = pat.replace("{name}", name).replace("{base}", base)
        if cand.lower() != name.lower() and cand not in out:
            out.append(cand)
    return out


def resolve(
    changed: dict[str, list[str]],
    index: SourceIndex,
    tests_cfg: dict,
    policy: str,
    canary: list[str],
    production: bool = False,
    coverage_exempt: list[str] | None = None,
) -> dict:
    """Pure resolution logic (unit-tested). Returns the test plan dict."""
    overrides = {k.lower(): v for k, v in (tests_cfg.get("overrides") or {}).items()}
    exempt = {c.lower() for c in coverage_exempt or []}
    require = tests_cfg.get("require_companion", True)
    tests: list[str] = []
    covered_by: dict[str, list[str]] = {}
    untested: list[dict] = []
    apex_changed: list[dict] = []
    changed_tests: list[str] = []

    def add_test(name: str) -> None:
        if name.lower() not in {t.lower() for t in tests}:
            tests.append(name)

    for mtype in APEX_TYPES:
        patterns = tests_cfg["class_patterns"] if mtype == "ApexClass" else tests_cfg["trigger_patterns"]
        for member in sorted(set(changed.get(mtype, [])), key=str.lower):
            if mtype == "ApexClass" and index.is_test(member):
                changed_tests.append(member)
                add_test(index.class_name(member) or member)
                continue
            source = index.classes if mtype == "ApexClass" else index.triggers
            if member.lower() not in source:
                # In package.xml but not under source_path; there's no local source to match tests against.
                continue
            apex_changed.append(
                {
                    "name": member,
                    "type": mtype,
                    # Interfaces/enums (and configured exemptions) have no coverable lines.
                    "coverage_exempt": member.lower() in exempt
                    or (mtype == "ApexClass" and index.no_executable_code(member)),
                }
            )
            if member.lower() in overrides:
                wanted = overrides[member.lower()]
                searched = list(wanted)
            else:
                wanted = candidates(member, patterns)
                searched = wanted
            found = [index.class_name(t) for t in wanted if index.is_test(t)]
            found = [f for f in found if f]
            if found:
                covered_by[member] = found
                for t in found:
                    add_test(t)
            elif require:
                untested.append({"name": member, "type": mtype, "searched": searched})

    if policy == "none":
        level, tests = "NoTestRun", []
    elif policy == "local":
        level, tests = "RunLocalTests", []
    elif policy == "canary":
        for t in canary:
            add_test(t)
        level = "RunSpecifiedTests"
    else:  # specified
        level = "RunSpecifiedTests" if (apex_changed or changed_tests) else "NoTestRun"
        if level == "NoTestRun":
            tests = []
    if production and level == "NoTestRun":
        # Production rejects NoTestRun; run the local suite instead.
        level, tests = "RunLocalTests", []

    return {
        "policy": policy,
        "test_level": level,
        "tests": tests,
        "apex_changed": apex_changed,
        "changed_test_classes": changed_tests,
        "covered_by": covered_by,
        "untested": untested if policy != "none" else [],
    }


def synthetic_result(plan: dict) -> dict:
    """Deploy-result-shaped JSON so deploy_report.py renders the fail-fast error."""
    names = ", ".join(u["name"] for u in plan["untested"])
    failures = [
        {
            "componentType": u["type"],
            "fullName": u["name"],
            "problemType": "Error",
            "problem": "No companion test class found (looked for: "
            + ", ".join(u["searched"])
            + "). Add one, or map it under tests.overrides in sf-pipeline/pipeline.config.yml.",
        }
        for u in plan["untested"]
    ]
    return {
        "status": 1,
        "name": "MissingApexTestsError",
        "message": f"Changed Apex has no resolvable test class: {names}. "
        "Deployment was not attempted (no fallback to RunLocalTests).",
        "synthetic": True,
        "result": {
            "status": "Failed",
            "success": False,
            "numberComponentErrors": len(failures),
            "details": {"componentFailures": failures},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--package", required=True, help="delta package.xml")
    parser.add_argument("--env", required=True, help="environment key from pipeline.config.yml")
    parser.add_argument("--config", help="path to pipeline.config.yml")
    parser.add_argument("--source-path", help="override project.source_path")
    parser.add_argument("--out", help="write the test plan JSON here")
    parser.add_argument("--synthetic-result", help="write a failed deploy result here when classes are untested")
    parser.add_argument("--github-output", action="store_true")
    parser.add_argument("--annotate", action="store_true", help="emit ::error:: annotations for untested classes")
    args = parser.parse_args(argv)

    try:
        cfg = pipeline_config.load(args.config)
    except pipeline_config.ConfigError as exc:
        print(f"::error title=pipeline.config.yml::{exc}", file=sys.stderr)
        return 2
    if args.env not in cfg["environments"]:
        print(f"::error::unknown environment {args.env!r}", file=sys.stderr)
        return 2
    env = cfg["environments"][args.env]
    source_path = args.source_path or cfg["project"]["source_path"]

    package = Path(args.package)
    changed = read_package(package) if package.is_file() else {}
    plan = resolve(
        changed,
        SourceIndex.build(source_path),
        cfg["tests"],
        env["test_policy"],
        env.get("canary_tests") or [],
        production=bool(env.get("production")),
        coverage_exempt=cfg["quality"].get("coverage_exempt") or [],
    )
    plan["environment"] = args.env

    if args.out:
        Path(args.out).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    if plan["untested"] and args.synthetic_result:
        Path(args.synthetic_result).write_text(json.dumps(synthetic_result(plan), indent=2), encoding="utf-8")
    if plan["untested"] and args.annotate:
        for u in plan["untested"]:
            print(
                f"::error title=Missing Apex test ({u['type']} {u['name']})::"
                f"No test class found (looked for {', '.join(u['searched'])}). "
                "Add one or map it in tests.overrides; deploy aborted without falling back to RunLocalTests."
            )

    summary = (
        f"{args.env}: policy={plan['policy']} level={plan['test_level']} "
        f"apex_changed={len(plan['apex_changed'])} tests={len(plan['tests'])} untested={len(plan['untested'])}"
    )
    print(summary)
    if plan["tests"]:
        print("tests: " + " ".join(plan["tests"]))

    if args.github_output:
        target = os.environ.get("GITHUB_OUTPUT")
        if not target:
            print("::error::--github-output used but GITHUB_OUTPUT is not set", file=sys.stderr)
            return 2
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"test_level={plan['test_level']}\n")
            fh.write(f"tests={' '.join(plan['tests'])}\n")
            fh.write(f"untested={','.join(u['name'] for u in plan['untested'])}\n")
            fh.write(f"apex_changed={len(plan['apex_changed'])}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

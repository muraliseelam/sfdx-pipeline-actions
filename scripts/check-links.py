#!/usr/bin/env python3
"""Verify every relative Markdown link and heading anchor in this repo resolves.

Documentation is the product here as much as the workflows are: a consumer who
follows a dead link in the setup guide is as stuck as one hitting a broken
action. This runs offline and only looks at relative links — external URLs are
not fetched.
"""
import glob
import os
import re
import sys

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)


def slug(heading: str) -> str:
    """Reproduce GitHub's heading -> anchor transformation.

    Note each space becomes its own hyphen; runs are NOT collapsed. That is why
    `## Part 1 — Set up` yields `part-1--set-up` (the em dash is dropped, leaving
    two adjacent spaces).
    """
    s = re.sub(r"<[^>]+>", "", heading.strip()).lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return s.replace(" ", "-")


def anchors(path: str) -> set:
    with open(path, encoding="utf-8") as fh:
        return {slug(h) for h in HEADING.findall(fh.read())}


def main() -> int:
    problems = []
    files = [f for f in glob.glob("**/*.md", recursive=True) if ".git/" not in f]

    for f in files:
        with open(f, encoding="utf-8") as fh:
            body = fh.read()
        for target in LINK.findall(body):
            if target.startswith(("http://", "https://", "mailto:", "#!")):
                continue
            path, _, anchor = target.partition("#")
            resolved = (
                os.path.normpath(os.path.join(os.path.dirname(f), path))
                if path
                else f
            )
            if path and not os.path.exists(resolved):
                problems.append(f"{f}: link points at a file that does not exist -> {target}")
                continue
            if anchor and anchor not in anchors(resolved):
                problems.append(f"{f}: no heading matches the anchor -> {target}")

    if problems:
        for p in problems:
            print(p)
        return 1

    print(f"checked {len(files)} markdown files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

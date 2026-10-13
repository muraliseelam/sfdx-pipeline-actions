#!/usr/bin/env python3
"""Best-effort Jira Cloud integration for the SF pipeline.

Reads credentials from the environment so nothing sensitive is ever passed on
the command line or stored in the repo:

    JIRA_BASE_URL   e.g. https://acme.atlassian.net
    JIRA_EMAIL      the Atlassian account email
    JIRA_API_TOKEN  an API token (id.atlassian.com > Security > API tokens)

Design rules:
  * If any credential is missing, EVERY command is a silent no-op (exit 0), so
    the pipeline works unchanged before Jira is wired up.
  * Network / API errors are logged as warnings but never fail the build — a
    deploy must not be rolled back just because Jira was unreachable.

Subcommands:
    extract-key "<text>"            -> prints the first ISSUE key (e.g. PROJ-123)
    comment <key> "<body>"         -> adds a comment to the issue
    transition <key> "<status>"    -> moves the issue to the named status
    deploy <key> <env> <status> [url]
                                   -> comment ("Deployed to <env>") + transition
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def creds():
    base = (os.environ.get("JIRA_BASE_URL") or "").strip().rstrip("/")
    email = (os.environ.get("JIRA_EMAIL") or "").strip()
    token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
    if not (base and email and token):
        return None
    return base, email, token


def _auth_header(email, token):
    raw = f"{email}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _request(method, url, email, token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", _auth_header(email, token))
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8") or "{}"
        return resp.status, (json.loads(body) if body.strip() else {})


def _warn(msg):
    print(f"::warning::[jira] {msg}")


def extract_key(text):
    m = KEY_RE.search(text or "")
    return m.group(1) if m else ""


def cmd_extract_key(text):
    print(extract_key(text))


def _adf(body):
    """Wrap plain text in Atlassian Document Format (required by API v3)."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": body}]}
        ],
    }


def add_comment(base, email, token, key, body):
    url = f"{base}/rest/api/3/issue/{key}/comment"
    try:
        status, _ = _request("POST", url, email, token, {"body": _adf(body)})
        if status >= 300:
            _warn(f"comment on {key} returned HTTP {status}")
        else:
            print(f"[jira] commented on {key}")
    except urllib.error.HTTPError as e:
        _warn(f"comment on {key} failed: HTTP {e.code} {e.reason}")
    except Exception as e:  # noqa: BLE001 - best effort
        _warn(f"comment on {key} failed: {e}")


def do_transition(base, email, token, key, status_name):
    if not status_name:
        return
    want = status_name.strip().lower()
    list_url = f"{base}/rest/api/3/issue/{key}/transitions"
    try:
        _, data = _request("GET", list_url, email, token)
    except Exception as e:  # noqa: BLE001
        _warn(f"could not list transitions for {key}: {e}")
        return
    match = None
    for t in data.get("transitions", []) or []:
        to_name = (t.get("to", {}) or {}).get("name", "")
        if to_name.strip().lower() == want or t.get("name", "").strip().lower() == want:
            match = t
            break
    if not match:
        avail = ", ".join(
            (t.get("to", {}) or {}).get("name", "?") for t in data.get("transitions", []) or []
        )
        _warn(f"no transition to '{status_name}' available from {key}'s current status "
              f"(available: {avail or 'none'}) — leaving status unchanged")
        return
    try:
        status, _ = _request("POST", list_url, email, token,
                             {"transition": {"id": match["id"]}})
        if status >= 300:
            _warn(f"transition {key} -> '{status_name}' returned HTTP {status}")
        else:
            print(f"[jira] moved {key} -> {status_name}")
    except urllib.error.HTTPError as e:
        _warn(f"transition {key} -> '{status_name}' failed: HTTP {e.code} {e.reason}")
    except Exception as e:  # noqa: BLE001
        _warn(f"transition {key} -> '{status_name}' failed: {e}")


def keys_for_fixversion(base, email, token, version, extra_jql=""):
    """Return the issue keys assigned to a given fixVersion (release label)."""
    jql = f'fixVersion = "{version}"'
    if extra_jql.strip():
        jql += f' AND ({extra_jql.strip()})'
    jql += " ORDER BY key ASC"
    params = urllib.parse.urlencode({"jql": jql, "fields": "key", "maxResults": "200"})
    url = f"{base}/rest/api/3/search/jql?{params}"
    try:
        status, data = _request("GET", url, email, token)
    except urllib.error.HTTPError as e:
        _warn(f"search for fixVersion '{version}' failed: HTTP {e.code} {e.reason}")
        return []
    except Exception as e:  # noqa: BLE001
        _warn(f"search for fixVersion '{version}' failed: {e}")
        return []
    if status >= 300:
        _warn(f"search for fixVersion '{version}' returned HTTP {status}")
        return []
    return [i.get("key", "") for i in data.get("issues", []) if i.get("key")]


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return

    cmd = argv[0]

    # extract-key never needs credentials.
    if cmd == "extract-key":
        cmd_extract_key(argv[1] if len(argv) > 1 else "")
        return

    c = creds()
    if not c:
        print("[jira] credentials not configured — skipping (no-op).")
        return
    base, email, token = c

    if cmd == "comment":
        key, body = argv[1], argv[2]
        if not key:
            print("[jira] no issue key — skipping comment.")
            return
        add_comment(base, email, token, key, body)

    elif cmd == "transition":
        key, status_name = argv[1], argv[2]
        if not key:
            print("[jira] no issue key — skipping transition.")
            return
        do_transition(base, email, token, key, status_name)

    elif cmd == "deploy":
        key = argv[1]
        env = argv[2]
        status_name = argv[3] if len(argv) > 3 else ""
        run_url = argv[4] if len(argv) > 4 else ""
        if not key:
            print("[jira] no issue key found in ref — skipping Jira update.")
            return
        body = f"✅ Deployed to *{env}* via GitHub Actions."
        if run_url:
            body += f" Run: {run_url}"
        add_comment(base, email, token, key, body)
        do_transition(base, email, token, key, status_name)

    elif cmd == "keys-for-fixversion":
        version = argv[1] if len(argv) > 1 else ""
        extra = argv[2] if len(argv) > 2 else ""
        if not version:
            print("[jira] no fixVersion supplied — nothing to gather.")
            return
        for key in keys_for_fixversion(base, email, token, version, extra):
            print(key)

    else:
        print(f"[jira] unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

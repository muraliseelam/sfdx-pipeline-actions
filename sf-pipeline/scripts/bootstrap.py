#!/usr/bin/env python3
"""One-stop setup for the Salesforce DevOps pipeline.

  init    --envs dev,int,qa,stage,prod
          Write your environments (in promotion order) into pipeline.config.yml.

  github  [--reviewer LOGIN ...]
          Configure the GitHub repo from the config, idempotently: merge settings,
          labels, environment branches, GitHub Environments and branch protection.

  secrets [--env ENV ...] [--org ENV=ALIAS ...] [--pat-from-gh | --pat-stdin]
          [--jwt-key server.key --consumer-key KEY [--username ENV=USER ...]] [--prod-repo-level]
          Copy org credentials from your local Salesforce CLI into GitHub secrets.
          sfdx_url envs: the auth URL of `sf org login web --alias <org_alias>`.
          jwt envs:      username + consumer key + private key.
          Production secrets go into the production GitHub Environments (only
          protected branches can read them) unless --prod-repo-level is given.
          Secret values are never printed.

  jwt-setup --env ENV [--org ALIAS] [--profile NAME] [--update-config] [--no-secrets]
          Turn on JWT for an environment in one go: create a key pair (kept in
          ~/.sf-pipeline, never in the repo), deploy a pre-authorized External
          Client App with that certificate into the org (using your existing CLI
          login), prove a JWT login works, then store USERNAME / CONSUMER_KEY /
          JWT_KEY secrets. --update-config sets auth_method: jwt for the env.

  doctor  Check config, tools, GitHub setup and secrets; print the fix for anything missing.

  setup   [secrets options]
          github + secrets + doctor in one go: the fastest path for a new repo.

  scratch-orgs --dev-hub ALIAS [--days 30] [--env ENV ...]
          Try the pipeline without sandboxes: create one scratch org per environment
          (aliased to each env's org_alias) from config/project-scratch-def.json.

Common options: --repo OWNER/NAME (default: project.repository or the current gh repo),
--config PATH, --dry-run (show what would change).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets as pysecrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import branch_protection  # noqa: E402
import pipeline_config  # noqa: E402
from gh_helpers import GhError, gh_api, parse_json_stream  # noqa: E402

LABEL_COLORS = {"approval": "0e8a16", "promote": "1d76db", "deploy": "d93f0b", "bypass": "b60205"}


class SetupError(Exception):
    """A setup step can't continue; the message says how to fix it."""


# ---------------------------------------------------------------------- helpers


def say(tag: str, message: str) -> None:
    print(f"  [{tag:<4}] {message}")


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        hints = {
            "gh": "install GitHub CLI (https://cli.github.com) and run `gh auth login`",
            "sf": "install Salesforce CLI: npm install --global @salesforce/cli",
            "git": "install git",
        }
        raise SetupError(f"`{name}` not found on PATH: {hints.get(name, '')}")
    return path


def run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8")


def api_optional(path: str) -> dict | list | None:
    """GET that returns None on 404 instead of raising."""
    proc = run([tool("gh"), "api", "-H", "Accept: application/vnd.github+json", path])
    if proc.returncode == 0:
        return parse_json_stream(proc.stdout)
    if "404" in proc.stderr or "Not Found" in proc.stderr:
        return None
    raise GhError(f"GET {path}: {proc.stderr.strip()}")


def resolve_repo(explicit: str | None, cfg: dict) -> str:
    repo = explicit or cfg["project"].get("repository") or ""
    if not repo:
        proc = run([tool("gh"), "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
        repo = proc.stdout.strip()
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        raise SetupError("can't tell which GitHub repo to use: pass --repo OWNER/NAME or set project.repository")
    return repo


def wanted_labels(cfg: dict) -> dict[str, tuple[str, str]]:
    """name -> (color, description) for every label the pipeline uses."""
    envs, chain = cfg["environments"], cfg["promotion"]["chain"]
    prefixes = cfg["github"]["labels"]
    labels = {
        cfg["approvals"]["label"]: (LABEL_COLORS["approval"], "Approves the PR for merge/deploy (sf-approval)"),
        cfg["quality"]["bypass_label"]: (LABEL_COLORS["bypass"], "Admin only: waive the coverage + code-analysis gates"),
    }
    for name in chain[1:]:
        if not envs[name]["production"]:
            labels[f"{prefixes['promote_prefix']}{name}"] = (
                LABEL_COLORS["promote"],
                f"After merge, open a promotion PR into {envs[name]['branch']}",
            )
    for name in chain:
        if not envs[name]["production"]:
            labels[f"{prefixes['deploy_prefix']}{name}"] = (
                LABEL_COLORS["deploy"],
                f"Deploy this PR's head to the {name} org before merging",
            )
    return labels


# ------------------------------------------------------------------------- init


def cmd_init(args: argparse.Namespace) -> int:
    names = [n.strip() for n in args.envs.split(",") if n.strip()]
    pipeline_config.from_dict({"environments": {n: {} for n in names}})  # validates names/count
    path = pipeline_config.config_path(args.config)
    block = "environments:\n" + "".join(f"  {n}: {{}}\n" for n in names)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        new, count = re.subn(r"(?m)^environments:[^\n]*\n(?:(?:[ \t]+[^\n]*)?\n)*", block + "\n", text, count=1)
        if not count:
            new = text.rstrip() + "\n\n" + block
    else:
        new = f"version: 1\n\n{block}\nproject:\n  source_path: force-app\n  baseline_branch: main\n"
    if args.dry_run:
        print(block)
        return 0
    path.write_text(new, encoding="utf-8", newline="\n")
    cfg = pipeline_config.load(path)
    chain = cfg["promotion"]["chain"]
    print(f"Wrote {len(chain)} environments to {path}: {' -> '.join(chain)} (production: {chain[-1]})")
    print("Next: python sf-pipeline/scripts/bootstrap.py github")
    return 0


# ----------------------------------------------------------------------- github


def ensure_repo_settings(repo: str, dry: bool) -> None:
    info = gh_api(f"repos/{repo}")
    if not (info.get("permissions") or {}).get("admin"):
        raise SetupError(f"your GitHub login needs admin rights on {repo}")
    want = {
        "allow_squash_merge": True,
        "allow_merge_commit": True,
        "allow_rebase_merge": False,
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
    }
    diff = {k: v for k, v in want.items() if info.get(k) != v}
    if not diff:
        say("ok", "merge settings (squash + merge commit, auto-merge, delete merged branches)")
        return
    if not dry:
        gh_api(f"repos/{repo}", method="PATCH", body=diff)
    say("set", "merge settings: " + ", ".join(f"{k}={str(v).lower()}" for k, v in diff.items()))


def ensure_labels(repo: str, cfg: dict, dry: bool) -> None:
    # GitHub label names are case-insensitive: "Approved" already counts as "approved".
    existing = {lbl["name"].lower() for lbl in gh_api(f"repos/{repo}/labels?per_page=100", paginate=True) or []}
    for name, (color, desc) in wanted_labels(cfg).items():
        if name.lower() in existing:
            say("ok", f"label {name}")
            continue
        if not dry:
            gh_api(f"repos/{repo}/labels", method="POST", body={"name": name, "color": color, "description": desc})
        say("add", f"label {name}")


def ensure_branches(repo: str, cfg: dict, dry: bool) -> None:
    baseline = cfg["project"]["baseline_branch"]
    base = api_optional(f"repos/{repo}/git/ref/heads/{baseline}")
    if not base:
        raise SetupError(f"baseline branch '{baseline}' doesn't exist in {repo}; push it first")
    sha = base["object"]["sha"]
    for name in cfg["promotion"]["chain"]:
        branch = cfg["environments"][name]["branch"]
        if branch == baseline:
            continue
        if api_optional(f"repos/{repo}/git/ref/heads/{branch}"):
            say("ok", f"branch {branch}")
            continue
        if not dry:
            gh_api(f"repos/{repo}/git/refs", method="POST", body={"ref": f"refs/heads/{branch}", "sha": sha})
        say("add", f"branch {branch} (from {baseline} @ {sha[:7]})")


PROTECTED_POLICY = {"protected_branches": True, "custom_branch_policies": False}


def environment_settings(current: dict) -> tuple[dict | None, list[dict], dict]:
    """(branch policy, reviewers as PUT entries, other rules) from a GET /environments/{name}."""
    reviewers: list[dict] = []
    other: dict = {}
    for rule in current.get("protection_rules") or []:
        if rule.get("type") == "required_reviewers":
            reviewers = [{"type": r.get("type"), "id": (r.get("reviewer") or {}).get("id")} for r in rule.get("reviewers") or []]
            if "prevent_self_review" in rule:
                other["prevent_self_review"] = rule["prevent_self_review"]
        elif rule.get("type") == "wait_timer":
            other["wait_timer"] = rule.get("wait_timer", 0)
    return current.get("deployment_branch_policy"), reviewers, other


def ensure_environment(repo: str, name: str, protected: bool, reviewers: list[int], dry: bool) -> None:
    """Create the environment, or repair it when it exists without the required protection
    (GitHub auto-creates environments, unprotected, the first time a job references them)."""
    path = f"repos/{repo}/environments/{quote(name, safe='')}"
    current = api_optional(path)
    wanted_reviewers = [{"type": "User", "id": uid} for uid in reviewers]
    if current:
        policy, have_reviewers, other = environment_settings(current)
        missing = [r for r in wanted_reviewers if r["id"] not in {h["id"] for h in have_reviewers}]
        if (not protected or policy == PROTECTED_POLICY) and not missing:
            say("ok", f"environment {name}")
            return
        body: dict = dict(other)
        body["deployment_branch_policy"] = PROTECTED_POLICY if protected else policy
        if have_reviewers or missing:
            body["reviewers"] = have_reviewers + missing
        if not dry:
            gh_api(path, method="PUT", body=body)
        say("set", f"environment {name} (protected branches only{', reviewers' if body.get('reviewers') else ''})")
        return
    body = {}
    if protected:
        body["deployment_branch_policy"] = PROTECTED_POLICY
    if wanted_reviewers:
        body["reviewers"] = wanted_reviewers
    if not dry:
        gh_api(path, method="PUT", body=body)
    extra = " (protected branches only" + (", reviewers" if reviewers else "") + ")" if protected else ""
    say("add", f"environment {name}{extra}")


def ensure_environments(repo: str, cfg: dict, reviewer_logins: list[str], dry: bool) -> None:
    reviewers = [int(gh_api(f"users/{login}")["id"]) for login in reviewer_logins]
    for name in cfg["promotion"]["chain"]:
        env = cfg["environments"][name]
        if env["production"]:
            ensure_environment(repo, env["validation_environment"], True, [], dry)
            ensure_environment(repo, env["github_environment"], True, reviewers, dry)
        else:
            ensure_environment(repo, env["github_environment"], False, [], dry)


def ensure_protection(repo: str, cfg: dict, dry: bool) -> None:
    for name in cfg["promotion"]["chain"]:
        branch = cfg["environments"][name]["branch"]
        body = branch_protection.desired_protection(cfg, name)
        current = branch_protection.get_protection(repo, branch)
        if current and branch_protection.to_put_body(current) == body:
            say("ok", f"protection on {branch}")
            continue
        if not dry:
            branch_protection.put_protection(repo, branch, body, signatures=False)
        checks = ", ".join(c["context"] for c in body["required_status_checks"]["checks"])
        say("set", f"protection on {branch} (requires {checks})")


def cmd_github(args: argparse.Namespace, cfg: dict, repo: str) -> int:
    print(f"GitHub setup for {repo}{' (dry run)' if args.dry_run else ''}")
    ensure_repo_settings(repo, args.dry_run)
    ensure_labels(repo, cfg, args.dry_run)
    ensure_branches(repo, cfg, args.dry_run)
    ensure_environments(repo, cfg, args.reviewer or [], args.dry_run)
    if args.skip_protection:
        say("skip", "branch protection (--skip-protection)")
    else:
        ensure_protection(repo, cfg, args.dry_run)
    print("Next: python sf-pipeline/scripts/bootstrap.py secrets   (then: bootstrap.py doctor)")
    return 0


# ---------------------------------------------------------------------- secrets


def parse_pairs(items: list[str] | None, what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        for part in item.split(","):
            if not part.strip():
                continue
            if "=" not in part:
                raise SetupError(f"{what} must look like ENV=VALUE (got {part!r})")
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def sf_auth_url(alias: str, instance_url: str) -> str:
    proc = run([tool("sf"), "org", "auth", "show-sfdx-auth-url", "--target-org", alias, "--json"])
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        data = {}
    url = (data.get("result") or {}).get("sfdxAuthUrl", "")
    if not url.startswith("force://"):
        reason = data.get("message") or "no auth URL"
        raise SetupError(
            f"no SFDX auth URL for org alias '{alias}' ({reason.splitlines()[0]}). "
            f"Log in with a browser first: sf org login web --alias {alias} --instance-url {instance_url} "
            "(JWT logins have no auth URL)"
        )
    return url


def sf_username(alias: str) -> str:
    proc = run([tool("sf"), "org", "display", "--target-org", alias, "--json"])
    try:
        user = (json.loads(proc.stdout or "{}").get("result") or {}).get("username", "")
    except ValueError:
        user = ""
    if not user:
        raise SetupError(f"can't read the username of org alias '{alias}'; pass --username ENV=USER")
    return user


def set_secret(repo: str, name: str, value: str, environment: str | None, dry: bool) -> None:
    where = f"environment {environment}" if environment else "repository"
    if not value:
        raise SetupError(f"refusing to set an empty secret {name}")
    if not dry:
        cmd = [tool("gh"), "secret", "set", name, "--repo", repo]
        if environment:
            cmd += ["--env", environment]
        # Bytes, not text: text-mode stdin on Windows would turn a PEM key's \n into \r\n.
        proc = subprocess.run(cmd, input=value.encode("utf-8"), capture_output=True)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise SetupError(f"gh secret set {name} ({where}) failed: {err}")
    say("set", f"{name} -> {where}")


def cmd_secrets(args: argparse.Namespace, cfg: dict, repo: str) -> int:
    envs = cfg["environments"]
    chain = cfg["promotion"]["chain"]
    selected = args.env or chain
    unknown = [e for e in selected if e not in envs]
    if unknown:
        raise SetupError(f"unknown environment(s): {', '.join(unknown)} (known: {', '.join(chain)})")
    aliases = parse_pairs(args.org, "--org")
    usernames = parse_pairs(args.username, "--username")
    print(f"Secrets for {repo}{' (dry run)' if args.dry_run else ''}; values are never printed")

    for name in selected:
        env = envs[name]
        alias = aliases.get(name, env["org_alias"])
        if env["production"] and not args.prod_repo_level:
            targets: list[str | None] = [env["validation_environment"], env["github_environment"]]
            for target in targets:
                ensure_environment(repo, target, True, [], args.dry_run)
        else:
            targets = [None]
        if env["auth_method"] == "sfdx_url":
            url = sf_auth_url(alias, env["instance_url"])
            for target in targets:
                set_secret(repo, env["secrets"]["auth_url"], url, target, args.dry_run)
        else:
            if not args.jwt_key or not args.consumer_key:
                raise SetupError(f"{name} uses jwt: pass --jwt-key server.key and --consumer-key <key>")
            key = Path(args.jwt_key).read_text(encoding="utf-8")
            if "-----BEGIN" not in key:
                raise SetupError(f"{args.jwt_key} isn't a PEM private key")
            user = usernames.get(name) or sf_username(alias)
            for target in targets:
                set_secret(repo, env["secrets"]["username"], user, target, args.dry_run)
                set_secret(repo, env["secrets"]["consumer_key"], args.consumer_key, target, args.dry_run)
                set_secret(repo, env["secrets"]["jwt_key"], key, target, args.dry_run)

    pat_name = cfg["github"]["pat_secret"]
    if args.pat_from_gh or args.pat_stdin:
        if args.pat_from_gh:
            token = run([tool("gh"), "auth", "token"]).stdout.strip()
        else:
            token = sys.stdin.read().strip()
        if not token:
            raise SetupError("no token to store for the PAT secret")
        set_secret(repo, pat_name, token, None, args.dry_run)
    else:
        say("skip", f"{pat_name} (pass --pat-from-gh or pipe a fine-grained PAT with --pat-stdin)")
    print("Next: python sf-pipeline/scripts/bootstrap.py doctor")
    return 0


# -------------------------------------------------------------------- jwt-setup

ECA_NAME = "SF_Pipeline_CI"
ECA_FILES = {
    "externalClientApps/{n}.eca-meta.xml": """<?xml version="1.0" encoding="UTF-8"?>
<ExternalClientApplication xmlns="http://soap.sforce.com/2006/04/metadata">
    <contactEmail>{email}</contactEmail>
    <description>Salesforce DevOps pipeline CI login (OAuth 2.0 JWT bearer flow)</description>
    <distributionState>Local</distributionState>
    <isProtected>false</isProtected>
    <label>SF Pipeline CI</label>
</ExternalClientApplication>
""",
    "extlClntAppOauthSettings/{n}_oauth.ecaOauth-meta.xml": """<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppOauthSettings xmlns="http://soap.sforce.com/2006/04/metadata">
    <commaSeparatedOauthScopes>Api, RefreshToken</commaSeparatedOauthScopes>
    <externalClientApplication>{n}</externalClientApplication>
    <label>SF Pipeline CI OAuth</label>
</ExtlClntAppOauthSettings>
""",
    "extlClntAppGlobalOauthSets/{n}_glbloauth.ecaGlblOauth-meta.xml": """<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppGlobalOauthSettings xmlns="http://soap.sforce.com/2006/04/metadata">
    <callbackUrl>http://localhost:1717/OauthRedirect</callbackUrl>
    <certificate>{cert}</certificate>
    <consumerKey>{consumer_key}</consumerKey>
    <externalClientApplication>{n}</externalClientApplication>
    <isConsumerSecretOptional>true</isConsumerSecretOptional>
    <isPkceRequired>false</isPkceRequired>
    <isSecretRequiredForRefreshToken>false</isSecretRequiredForRefreshToken>
    <label>SF Pipeline CI Global OAuth</label>
</ExtlClntAppGlobalOauthSettings>
""",
    "extlClntAppOauthPolicies/{n}_oauthPlcy.ecaOauthPlcy-meta.xml": """<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppOauthConfigurablePolicies xmlns="http://soap.sforce.com/2006/04/metadata">
    <commaSeparatedProfile>{profile}</commaSeparatedProfile>
    <externalClientApplication>{n}</externalClientApplication>
    <ipRelaxationPolicyType>Bypass</ipRelaxationPolicyType>
    <label>SF Pipeline CI OAuth Policies</label>
    <permittedUsersPolicyType>AdminApprovedPreAuthorized</permittedUsersPolicyType>
</ExtlClntAppOauthConfigurablePolicies>
""",
}


def key_dir(repo: str) -> Path:
    return Path.home() / ".sf-pipeline" / repo.replace("/", "__")


def ensure_key_pair(directory: Path, days: int) -> tuple[Path, Path]:
    key, cert = directory / "server.key", directory / "server.crt"
    if key.is_file() and cert.is_file():
        say("ok", f"key pair {key}")
        return key, cert
    directory.mkdir(parents=True, exist_ok=True)
    proc = run([tool("openssl"), "req", "-x509", "-sha256", "-nodes", "-days", str(days), "-newkey", "rsa:2048",
                "-keyout", str(key), "-out", str(cert), "-subj", "/CN=sf-pipeline-ci"])
    if proc.returncode != 0 or not key.is_file():
        raise SetupError(f"openssl couldn't create the key pair: {proc.stderr.strip()}")
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass
    say("add", f"key pair {key} (valid {days} days; keep it out of git)")
    return key, cert


def consumer_key_for(directory: Path, env: str) -> str:
    store = directory / "consumer_keys.json"
    keys = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}
    if env not in keys:
        keys[env] = f"SFPipeline{pysecrets.token_hex(24)}"
        store.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    return keys[env]


def deploy_eca(alias: str, cert: str, consumer_key: str, profile: str, email: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "sfdx-project.json").write_text(
            json.dumps({"packageDirectories": [{"path": "eca", "default": True}], "sourceApiVersion": "64.0"}),
            encoding="utf-8",
        )
        values = {"n": ECA_NAME, "cert": cert.strip(), "consumer_key": consumer_key, "profile": profile, "email": email}
        for rel, template in ECA_FILES.items():
            path = project / "eca" / "main" / "default" / rel.format(n=ECA_NAME)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(template.format(**values), encoding="utf-8")
        proc = subprocess.run(
            [tool("sf"), "project", "deploy", "start", "--source-dir", "eca", "--target-org", alias,
             "--ignore-conflicts", "--wait", "20", "--json"],
            cwd=project, capture_output=True, text=True, encoding="utf-8",
        )
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        data = {}
    result = data.get("result") or {}
    if result.get("status") != "Succeeded":
        failures = ((result.get("details") or {}).get("componentFailures")) or []
        failures = failures if isinstance(failures, list) else [failures]
        why = "; ".join(f"{f.get('fullName')}: {f.get('problem')}" for f in failures) or data.get("message") or proc.stderr
        raise SetupError(f"deploying the External Client App to '{alias}' failed: {why}")
    say("add", f"External Client App {ECA_NAME} in org '{alias}' (pre-authorized profile: {profile})")


def verify_jwt(consumer_key: str, key: Path, username: str, instance_url: str, attempts: int = 8) -> None:
    """Log in with JWT in a throwaway CLI home so your own logins are untouched."""
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ, HOME=home, USERPROFILE=home, SF_USE_GENERIC_UNIX_KEYCHAIN="true",
                   SF_DISABLE_TELEMETRY="true", SF_AUTOUPDATE_DISABLE="true")
        message = ""
        for attempt in range(1, attempts + 1):
            proc = subprocess.run(
                [tool("sf"), "org", "login", "jwt", "--client-id", consumer_key, "--jwt-key-file", str(key),
                 "--username", username, "--instance-url", instance_url, "--json"],
                capture_output=True, text=True, encoding="utf-8", env=env,
            )
            try:
                data = json.loads(proc.stdout or "{}")
            except ValueError:
                data = {}
            if data.get("status") == 0:
                say("ok", f"JWT login as {username} ({instance_url})")
                return
            message = data.get("message") or proc.stderr.strip()
            if attempt < attempts:
                time.sleep(30)  # new apps can take a few minutes to propagate
        raise SetupError(f"JWT login as {username} still fails after {attempts} tries: {message}")


def cmd_jwt_setup(args: argparse.Namespace, cfg: dict, repo: str) -> int:
    name = args.env
    if name not in cfg["environments"]:
        raise SetupError(f"unknown environment {name!r}")
    env = cfg["environments"][name]
    alias = args.org or env["org_alias"]
    print(f"JWT setup for {name} (org alias '{alias}'){' (dry run)' if args.dry_run else ''}")
    username = sf_username(alias)
    directory = key_dir(repo)
    key, cert = ensure_key_pair(directory, args.days)
    consumer_key = consumer_key_for(directory, name)
    if args.dry_run:
        say("plan", f"deploy {ECA_NAME} to '{alias}', verify JWT as {username}, set 3 secrets")
        return 0
    deploy_eca(alias, cert.read_text(encoding="utf-8"), consumer_key, args.profile, args.email)
    verify_jwt(consumer_key, key, username, env["instance_url"])
    if not args.no_secrets:
        targets: list[str | None] = (
            [env["validation_environment"], env["github_environment"]]
            if env["production"] and not args.prod_repo_level else [None]
        )
        for target in targets:
            if target:
                ensure_environment(repo, target, True, [], False)
            set_secret(repo, env["secrets"]["username"], username, target, False)
            set_secret(repo, env["secrets"]["consumer_key"], consumer_key, target, False)
            set_secret(repo, env["secrets"]["jwt_key"], key.read_text(encoding="utf-8"), target, False)
    if env["auth_method"] != "jwt":
        if args.update_config:
            path = pipeline_config.config_path(args.config)
            text = path.read_text(encoding="utf-8")
            new, count = re.subn(rf"(?m)^(\s+){re.escape(name)}:\s*\{{\s*\}}\s*$", rf"\1{name}: {{auth_method: jwt}}", text)
            if count:
                path.write_text(new, encoding="utf-8", newline="\n")
                say("set", f"auth_method: jwt for {name} in {path.name} (commit it)")
            else:
                say("todo", f"add `auth_method: jwt` under environments.{name} in {path.name}")
        else:
            say("todo", f"set `auth_method: jwt` under environments.{name} (or re-run with --update-config)")
    return 0


# ------------------------------------------------------------------ scratch-orgs


def cmd_scratch_orgs(args: argparse.Namespace, cfg: dict) -> int:
    definition = Path(args.definition)
    if not definition.is_file():
        raise SetupError(f"scratch org definition {definition} not found")
    names = args.env or cfg["promotion"]["chain"]
    print(f"Creating {len(names)} scratch org(s) from Dev Hub '{args.dev_hub}' ({args.days} days){' (dry run)' if args.dry_run else ''}")
    for name in names:
        if name not in cfg["environments"]:
            raise SetupError(f"unknown environment {name!r}")
        alias = cfg["environments"][name]["org_alias"]
        existing = run([tool("sf"), "org", "display", "--target-org", alias, "--json"])
        try:
            found = json.loads(existing.stdout or "{}").get("status") == 0
        except ValueError:
            found = False
        if found:
            say("ok", f"{name}: org alias '{alias}' already exists")
            continue
        if args.dry_run:
            say("plan", f"{name}: create scratch org '{alias}'")
            continue
        proc = run([tool("sf"), "org", "create", "scratch", "--definition-file", str(definition), "--alias", alias,
                    "--duration-days", str(args.days), "--target-dev-hub", args.dev_hub, "--wait", "30", "--json"])
        try:
            data = json.loads(proc.stdout or "{}")
        except ValueError:
            data = {}
        if data.get("status") != 0:
            raise SetupError(f"{name}: scratch org creation failed: {data.get('message') or proc.stderr.strip()}")
        say("add", f"{name}: scratch org '{alias}' ({(data.get('result') or {}).get('username', '?')})")
    print("Next: python sf-pipeline/scripts/bootstrap.py setup --pat-from-gh")
    return 0


# ----------------------------------------------------------------------- doctor


def secret_names(repo: str, environment: str | None = None) -> set[str]:
    cmd = [tool("gh"), "secret", "list", "--repo", repo, "--json", "name"]
    if environment:
        cmd += ["--env", environment]
    proc = run(cmd)
    if proc.returncode != 0:
        return set()
    return {s["name"] for s in json.loads(proc.stdout or "[]")}


def cmd_doctor(args: argparse.Namespace, cfg: dict, repo: str) -> int:
    problems = 0

    def check(ok: bool, what: str, fix: str = "") -> None:
        nonlocal problems
        if ok:
            say("ok", what)
        else:
            problems += 1
            say("FAIL", f"{what}" + (f"\n           fix: {fix}" if fix else ""))

    chain = cfg["promotion"]["chain"]
    envs = cfg["environments"]
    print(f"Pipeline doctor for {repo}")
    check(True, f"config: {' -> '.join(chain)} (production: {chain[-1]})")
    for name in ("git", "gh"):
        check(shutil.which(name) is not None, f"{name} installed", f"install {name}")
    if shutil.which("sf") is None:
        say("warn", "sf CLI not installed locally (only needed for `bootstrap.py secrets`)")

    info = api_optional(f"repos/{repo}")
    check(bool(info), f"repo {repo} reachable", "gh auth login, and check --repo")
    if not info:
        return 1
    check(bool((info.get("permissions") or {}).get("admin")), "admin rights on the repo", "ask a repo admin to run setup")
    for key, want in (("allow_squash_merge", True), ("allow_merge_commit", True), ("allow_auto_merge", True)):
        check(info.get(key) is want, f"repo setting {key}={str(want).lower()}", "bootstrap.py github")

    labels = {lbl["name"] for lbl in gh_api(f"repos/{repo}/labels?per_page=100", paginate=True) or []}
    missing_labels = [n for n in wanted_labels(cfg) if n not in labels]
    check(not missing_labels, "labels", f"bootstrap.py github (missing: {', '.join(missing_labels)})")

    for name in chain:
        env = envs[name]
        branch = env["branch"]
        exists = bool(api_optional(f"repos/{repo}/git/ref/heads/{branch}"))
        check(exists, f"{name}: branch {branch}", "bootstrap.py github")
        if exists:
            prot = branch_protection.get_protection(repo, branch)
            got = {c["context"] for c in ((prot or {}).get("required_status_checks") or {}).get("checks") or []}
            need = {env["status_check"], cfg["approvals"]["status_check"]}
            check(need <= got, f"{name}: protection requires {', '.join(sorted(need))}", "bootstrap.py github")
        gh_envs = [env["github_environment"]] + ([env["validation_environment"]] if env["production"] else [])
        for gh_env in gh_envs:
            current = api_optional(f"repos/{repo}/environments/{quote(gh_env, safe='')}")
            if env["production"]:
                check(bool(current), f"{name}: GitHub environment {gh_env}", "bootstrap.py github")
                if current:
                    policy, reviewers, _ = environment_settings(current)
                    check(policy == PROTECTED_POLICY, f"{name}: {gh_env} limited to protected branches", "bootstrap.py github")
                    if gh_env == env["github_environment"] and not reviewers:
                        say("warn", f"{name}: {gh_env} has no required reviewers (the approval label is the only gate); "
                                    "add some with: bootstrap.py github --reviewer <login>")
            elif not current:
                say("info", f"{name}: GitHub environment {gh_env} will be created on first deploy")

    repo_secrets = secret_names(repo)
    for name in chain:
        env = envs[name]
        needed = pipeline_config.required_secrets(cfg, name)
        if env["production"]:
            scopes = {e: secret_names(repo, e) for e in (env["validation_environment"], env["github_environment"])}
            ok = all(s in repo_secrets or all(s in sc for sc in scopes.values()) for s in needed)
        else:
            ok = all(s in repo_secrets for s in needed)
        check(ok, f"{name}: secret(s) {', '.join(needed)} ({env['auth_method']})", f"bootstrap.py secrets --env {name}")
    pat = cfg["github"]["pat_secret"]
    check(pat in repo_secrets, f"PAT secret {pat}", "bootstrap.py secrets --pat-from-gh (or --pat-stdin < token.txt)")
    admin = cfg["github"]["admin_pat_secret"]
    if admin != pat:
        prod = envs[pipeline_config.production_env(cfg)]
        in_env = admin in secret_names(repo, prod["validation_environment"])
        check(admin in repo_secrets or in_env, f"admin PAT secret {admin} (used by the env-branch sync)",
              f"gh secret set {admin} --repo {repo} --env {prod['validation_environment']}")
    chat = cfg["notifications"]["chat"]
    if chat["enabled"]:
        api = [chat["api_url_secret"], chat["api_token_secret"], chat["channel_secret"]]
        ok = chat["webhook_secret"] in repo_secrets or all(s in repo_secrets for s in api)
        check(ok, f"chat secret {chat['webhook_secret']} (or {', '.join(api)})",
              f"gh secret set {chat['webhook_secret']} --repo {repo}")

    print(f"\n{'All good.' if not problems else f'{problems} problem(s) found.'}")
    return 1 if problems else 0


# -------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="OWNER/NAME (default: project.repository, else the current gh repo)")
    parser.add_argument("--config", help="path to pipeline.config.yml")
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write environments into the config")
    p.add_argument("--envs", required=True, help="comma-separated, in promotion order (last = production)")

    p = sub.add_parser("github", help="configure the GitHub repo")
    p.add_argument("--reviewer", action="append", help="GitHub login required to approve production deploys")
    p.add_argument("--skip-protection", action="store_true")

    def secrets_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--env", action="append", help="only these environments (repeatable)")
        p.add_argument("--org", action="append", help="ENV=ALIAS when your local alias differs from org_alias")
        p.add_argument("--jwt-key", help="private key file for jwt environments")
        p.add_argument("--consumer-key", help="consumer key for jwt environments")
        p.add_argument("--username", action="append", help="ENV=USERNAME for jwt environments")
        p.add_argument("--prod-repo-level", action="store_true", help="store production secrets at repo level")
        pat = p.add_mutually_exclusive_group()
        pat.add_argument("--pat-from-gh", action="store_true", help="store your `gh auth token` as the PAT secret")
        pat.add_argument("--pat-stdin", action="store_true", help="read a fine-grained PAT from stdin")

    secrets_options(sub.add_parser("secrets", help="push org credentials into GitHub secrets"))

    p = sub.add_parser("setup", help="github + secrets + doctor in one go")
    secrets_options(p)
    p.add_argument("--reviewer", action="append", help="GitHub login required to approve production deploys")
    p.add_argument("--skip-protection", action="store_true")

    p = sub.add_parser("scratch-orgs", help="create one scratch org per environment to try the pipeline")
    p.add_argument("--dev-hub", required=True, help="alias of your Dev Hub org")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--env", action="append", help="only these environments (repeatable)")
    p.add_argument("--definition", default="config/project-scratch-def.json")

    p = sub.add_parser("jwt-setup", help="create the JWT External Client App + secrets for one env")
    p.add_argument("--env", required=True)
    p.add_argument("--org", help="local org alias (default: the env's org_alias)")
    p.add_argument("--profile", default="System Administrator", help="profile pre-authorized for the app")
    p.add_argument("--email", default="noreply@example.com", help="contact email recorded on the app")
    p.add_argument("--days", type=int, default=730, help="certificate validity when a new key pair is created")
    p.add_argument("--update-config", action="store_true", help="set auth_method: jwt for the env in the config")
    p.add_argument("--no-secrets", action="store_true", help="don't write GitHub secrets")
    p.add_argument("--prod-repo-level", action="store_true", help="store production secrets at repo level")

    sub.add_parser("doctor", help="check everything and print fixes")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return cmd_init(args)
        cfg = pipeline_config.load(args.config)
        repo = resolve_repo(args.repo, cfg)
        if args.command == "github":
            return cmd_github(args, cfg, repo)
        if args.command == "secrets":
            return cmd_secrets(args, cfg, repo)
        if args.command == "jwt-setup":
            return cmd_jwt_setup(args, cfg, repo)
        if args.command == "scratch-orgs":
            return cmd_scratch_orgs(args, cfg)
        if args.command == "setup":
            cmd_github(args, cfg, repo)
            print()
            cmd_secrets(args, cfg, repo)
            print()
            return cmd_doctor(args, cfg, repo)
        if args.command == "doctor":
            return cmd_doctor(args, cfg, repo)
    except (SetupError, pipeline_config.ConfigError, GhError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())

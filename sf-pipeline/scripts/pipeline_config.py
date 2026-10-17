#!/usr/bin/env python3
"""Load, normalize, validate and query sf-pipeline/pipeline.config.yml.

The config is convention-over-configuration: the only required input is the
list of environments in promotion order. Everything else (branch, org alias,
secret names, status check, GitHub environment, instance URL, test policy,
approvals) defaults from the environment name and can be overridden per env.
The last environment is production unless another one sets `production: true`.

Every workflow, composite action and helper script reads the *normalized*
config through this module, so nothing about an environment is hardcoded.

Commands
  validate                                   Validate the config (prints the resolved chain).
  show                                       Print the fully-resolved config as YAML.
  env (--name ENV | --branch BR | --label LABEL | --production)
                                             Flat view of one environment + globals.
  envs [--sync-only]                         JSON list of environment keys, in promotion order.
  promote-plan --base-branch BR (--labels L1,L2 | --target ENV)
                                             Resolve promotion targets for a merged PR.
  get DOTTED.KEY                             Print one resolved value.

--github-output appends key=value pairs to $GITHUB_OUTPUT instead of printing.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    sys.exit(
        f"PyYAML is not installed for this Python ({sys.executable}). Install it with:\n"
        f'  "{sys.executable}" -m pip install --user pyyaml\n'
        "then run the same command again with this same Python."
    )

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "pipeline.config.yml"
TEST_POLICIES = ("specified", "local", "canary", "none")
PRODUCTION_POLICIES = ("specified", "local", "canary")
AUTH_METHODS = ("sfdx_url", "jwt")
SECRET_KEYS = ("auth_url", "username", "consumer_key", "jwt_key")
ENV_KEYS = {
    "production",
    "branch",
    "org_alias",
    "instance_url",
    "auth_method",
    "secrets",
    "status_check",
    "test_policy",
    "canary_tests",
    "required_approvals",
    "github_environment",
    "validation_environment",
    "sync_after_release",
    "jira_status",
}
ENV_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,30}$")

# Defaults for every top-level section; the user's file is deep-merged over these.
DEFAULTS: dict[str, Any] = {
    "project": {"source_path": "force-app", "baseline_branch": "main", "repository": ""},
    "auth": {"method": "sfdx_url"},
    "quality": {
        "coverage_threshold": 75,
        "coverage_exempt": [],
        "code_analyzer": {"enabled": True, "severity_threshold": 2, "rule_selector": "Recommended"},
        "dependency_lint": True,
        "bypass_label": "bypass-quality-gates",
    },
    "tests": {
        "require_companion": True,
        "class_patterns": ["{name}Test", "{name}_Test", "{name}Tests", "Test{name}", "Test_{name}"],
        "trigger_patterns": [
            "{name}Test",
            "{name}_Test",
            "{base}TriggerTest",
            "{base}TriggerHandlerTest",
            "{base}HandlerTest",
            "{base}Test",
        ],
        "overrides": {},
    },
    "approvals": {
        "label": "approved",
        "required_reviews": 1,
        "dismiss_label_on_push": True,
        "status_check": "sf-approval",
        "auto_merge": True,
    },
    "deploy": {"wait_minutes": 120, "delta_output_dir": ".sgd", "ignore_warnings": False, "profiles_mode": "full"},
    "tooling": {
        "node_version": "22",
        "sf_cli_version": "2.153.5",
        "sgd_version": "6.45.1",
        "code_analyzer_version": "5.16.0",
    },
    "github": {
        "pat_secret": "SF_PIPELINE_PAT",
        "admin_pat_secret": "",
        "status_check_app_id": -1,
        "labels": {"promote_prefix": "promote:", "deploy_prefix": "deploy:"},
    },
    "promotion": {"enforce_order": True, "branch_prefix": "promote/"},
    "release": {"source_env": None, "branch_prefix": "release/", "tag_prefix": "v", "quick_deploy": True},
    "notifications": {
        "chat": {
            "enabled": False,
            "provider": "auto",
            "webhook_secret": "CHAT_WEBHOOK_URL",
            "api_url_secret": "CHAT_API_URL",
            "api_token_secret": "CHAT_API_TOKEN",
            "channel_secret": "CHAT_CHANNEL_ID",
            "deploy_success": True,
        },
        "jira": {
            "enabled": False,
            "base_url_secret": "JIRA_BASE_URL",
            "email_secret": "JIRA_EMAIL",
            "token_secret": "JIRA_API_TOKEN",
        },
    },
    "audit": {"enabled": True, "branch": "audit-log"},
}


class ConfigError(Exception):
    """pipeline.config.yml is missing or invalid."""


# ----------------------------------------------------------------- normalization


def secret_prefix(env_name: str) -> str:
    """SF_<ENV> with anything that isn't a letter/digit turned into '_'."""
    return "SF_" + re.sub(r"[^A-Za-z0-9]", "_", env_name).upper()


def _merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = _merge(base.get(key), value) if key in base else copy.deepcopy(value)
        return out
    return copy.deepcopy(override) if override is not None else copy.deepcopy(base)


def normalize(raw: dict) -> tuple[dict, list[str]]:
    """Fill every default. Returns (config, structural errors)."""
    errs: list[str] = []
    # Deep-copy the defaults: normalize() mutates nested sections (chain, release.source_env).
    cfg = _merge(copy.deepcopy(DEFAULTS), {k: v for k, v in raw.items() if k != "environments"})
    envs_raw = raw.get("environments")
    if not isinstance(envs_raw, dict) or len(envs_raw) < 2:
        errs.append("environments must list at least two environments in promotion order (the last is production)")
        cfg["environments"] = {}
        cfg["promotion"]["chain"] = []
        return cfg, errs

    # YAML turns keys like `2024:` or `yes:` into int/bool; key everything by its string form.
    envs_raw = {str(k): v for k, v in envs_raw.items()}
    names = list(envs_raw)
    for n in names:
        if not ENV_NAME.match(n):
            errs.append(f"environment name {n!r} must start with a letter and use only letters, digits, '-' or '_'")
    flagged = [n for n in names if isinstance(envs_raw[n], dict) and envs_raw[n].get("production")]
    if len(flagged) > 1:
        errs.append(f"only one environment may set production: true (found {', '.join(flagged)})")
    production = flagged[0] if flagged else names[-1]
    if production != names[-1]:
        errs.append(f"production ({production}) must be the last environment in the list (promotion order)")

    baseline = cfg["project"]["baseline_branch"]
    default_approvals = cfg["approvals"]["required_reviews"]
    envs: dict[str, dict] = {}
    for name in names:
        given = envs_raw[name] if envs_raw[name] is not None else {}
        if not isinstance(given, dict):
            errs.append(f"environments.{name} must be a mapping (use {{}} for all defaults)")
            given = {}
        unknown = sorted(set(given) - ENV_KEYS)
        if unknown:
            errs.append(f"environments.{name} has unknown key(s): {', '.join(unknown)} (allowed: {', '.join(sorted(ENV_KEYS))})")
        is_prod = name == production
        prefix = secret_prefix(name)
        secrets = {key: f"{prefix}_{key.upper()}" for key in SECRET_KEYS}
        given_secrets = given.get("secrets") or {}
        if not isinstance(given_secrets, dict) or set(given_secrets) - set(SECRET_KEYS):
            errs.append(f"environments.{name}.secrets may only set: {', '.join(SECRET_KEYS)}")
        else:
            secrets.update(given_secrets)
        github_env = given.get("github_environment") or ("production" if is_prod else f"sf-{name}")
        envs[name] = {
            "production": is_prod,
            "branch": given.get("branch", baseline if is_prod else name),
            "org_alias": given.get("org_alias", name),
            "instance_url": given.get(
                "instance_url", "https://login.salesforce.com" if is_prod else "https://test.salesforce.com"
            ),
            "auth_method": given.get("auth_method", cfg["auth"]["method"]),
            "secrets": secrets,
            "status_check": given.get("status_check", f"deploy-{name}"),
            "test_policy": given.get("test_policy", "local" if is_prod else "specified"),
            "canary_tests": given.get("canary_tests", []),
            "required_approvals": given.get("required_approvals", default_approvals),
            "github_environment": github_env,
            "validation_environment": given.get("validation_environment", f"{github_env}-validate"),
            "sync_after_release": given.get("sync_after_release", not is_prod) and not is_prod,
            "jira_status": given.get("jira_status", ""),
        }
    cfg["environments"] = envs
    cfg["promotion"]["chain"] = names
    if not cfg["github"].get("admin_pat_secret"):
        cfg["github"]["admin_pat_secret"] = cfg["github"]["pat_secret"]
    if not cfg["release"].get("source_env"):
        cfg["release"]["source_env"] = names[-2]
    return cfg, errs


# --------------------------------------------------------------------------- load


def config_path(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get("SF_PIPELINE_CONFIG") or DEFAULT_CONFIG)


def load_raw(path: str | Path | None = None) -> tuple[dict, Path]:
    p = config_path(str(path) if path else None)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    with p.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} must contain a YAML mapping")
    return raw, p


def load(path: str | Path | None = None) -> dict:
    """Load, normalize and validate. Raises ConfigError listing every problem."""
    raw, p = load_raw(path)
    cfg, errors = normalize(raw)
    errors += validate(cfg, repo_root=p.resolve().parent.parent)
    if errors:
        raise ConfigError("invalid pipeline config:\n  - " + "\n  - ".join(dict.fromkeys(errors)))
    return cfg


def from_dict(raw: dict) -> dict:
    """Normalize + validate an in-memory config (used by tests and bootstrap)."""
    cfg, errors = normalize(copy.deepcopy(raw))
    errors += validate(cfg)
    if errors:
        raise ConfigError("invalid pipeline config:\n  - " + "\n  - ".join(dict.fromkeys(errors)))
    return cfg


# ----------------------------------------------------------------------- validate


def validate(cfg: dict, repo_root: Path | None = None) -> list[str]:
    """Problems in a normalized config (empty list when valid)."""
    errs: list[str] = []

    def is_int(v: Any) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)

    def need_str(obj: Any, key: str, where: str) -> str | None:
        val = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            val = str(val)
        if not isinstance(val, str) or not val.strip():
            errs.append(f"{where}.{key} must be a non-empty string")
            return None
        return val

    def need_bool(obj: dict, key: str, where: str) -> None:
        if not isinstance(obj.get(key), bool):
            errs.append(f"{where}.{key} must be true or false")

    project = cfg["project"]
    source_path = need_str(project, "source_path", "project")
    baseline = need_str(project, "baseline_branch", "project")

    if cfg["auth"].get("method") not in AUTH_METHODS:
        errs.append(f"auth.method must be one of: {', '.join(AUTH_METHODS)}")

    quality = cfg["quality"]
    threshold = quality.get("coverage_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 <= threshold <= 100:
        errs.append("quality.coverage_threshold must be a number between 0 and 100")
    exempt = quality.get("coverage_exempt")
    if not isinstance(exempt, list) or not all(isinstance(c, str) for c in exempt):
        errs.append("quality.coverage_exempt must be a list of class names")
    analyzer = quality.get("code_analyzer") if isinstance(quality.get("code_analyzer"), dict) else {}
    need_bool(analyzer, "enabled", "quality.code_analyzer")
    sev = analyzer.get("severity_threshold")
    if not is_int(sev) or not 1 <= sev <= 5:
        errs.append("quality.code_analyzer.severity_threshold must be 1 (Critical) .. 5 (Info)")
    need_str(analyzer, "rule_selector", "quality.code_analyzer")
    need_bool(quality, "dependency_lint", "quality")
    need_str(quality, "bypass_label", "quality")

    tests = cfg["tests"]
    need_bool(tests, "require_companion", "tests")
    for key in ("class_patterns", "trigger_patterns"):
        patterns = tests.get(key)
        if not isinstance(patterns, list) or not patterns:
            errs.append(f"tests.{key} must be a non-empty list")
            continue
        for pat in patterns:
            if not isinstance(pat, str) or ("{name}" not in pat and "{base}" not in pat):
                errs.append(f"tests.{key} entry {pat!r} must contain {{name}} or {{base}}")
    overrides = tests.get("overrides")
    if not isinstance(overrides, dict) or any(
        not isinstance(v, list) or not all(isinstance(t, str) for t in v) for v in overrides.values()
    ):
        errs.append("tests.overrides must map ClassName -> [TestClass, ...]")

    approvals = cfg["approvals"]
    need_str(approvals, "label", "approvals")
    need_str(approvals, "status_check", "approvals")
    need_bool(approvals, "dismiss_label_on_push", "approvals")
    need_bool(approvals, "auto_merge", "approvals")
    if not is_int(approvals.get("required_reviews")) or not 0 <= approvals["required_reviews"] <= 6:
        errs.append("approvals.required_reviews must be an integer 0-6")

    deploy = cfg["deploy"]
    if not is_int(deploy.get("wait_minutes")) or deploy["wait_minutes"] < 1:
        errs.append("deploy.wait_minutes must be an integer >= 1")
    need_str(deploy, "delta_output_dir", "deploy")
    need_bool(deploy, "ignore_warnings", "deploy")
    if deploy.get("profiles_mode") not in ("full", "incremental"):
        errs.append("deploy.profiles_mode must be full or incremental")

    for key in ("node_version", "sf_cli_version", "sgd_version", "code_analyzer_version"):
        need_str(cfg["tooling"], key, "tooling")

    github = cfg["github"]
    need_str(github, "pat_secret", "github")
    need_str(github, "admin_pat_secret", "github")
    if not is_int(github.get("status_check_app_id")):
        errs.append("github.status_check_app_id must be an integer (-1 = any source)")
    labels = github.get("labels") or {}
    need_str(labels, "promote_prefix", "github.labels")
    need_str(labels, "deploy_prefix", "github.labels")
    if labels.get("promote_prefix") == labels.get("deploy_prefix"):
        errs.append("github.labels.promote_prefix and deploy_prefix must differ")

    chat = (cfg.get("notifications") or {}).get("chat") or {}
    need_bool(chat, "enabled", "notifications.chat")
    need_bool(chat, "deploy_success", "notifications.chat")
    if chat.get("provider") not in ("auto", "slack", "teams", "webex", "generic", "api"):
        errs.append("notifications.chat.provider must be auto, slack, teams, webex, generic or api")
    for key in ("webhook_secret", "api_url_secret", "api_token_secret", "channel_secret"):
        need_str(chat, key, "notifications.chat")
    jira = (cfg.get("notifications") or {}).get("jira") or {}
    need_bool(jira, "enabled", "notifications.jira")
    for key in ("base_url_secret", "email_secret", "token_secret"):
        need_str(jira, key, "notifications.jira")
    audit = cfg.get("audit") or {}
    need_bool(audit, "enabled", "audit")
    need_str(audit, "branch", "audit")

    promotion = cfg["promotion"]
    need_bool(promotion, "enforce_order", "promotion")
    need_str(promotion, "branch_prefix", "promotion")

    envs = cfg["environments"]
    if not envs:
        return errs

    seen: dict[str, dict[str, str]] = {"branch": {}, "org_alias": {}, "status_check": {}}
    for name, env in envs.items():
        where = f"environments.{name}"
        for key in ("branch", "org_alias", "instance_url", "status_check", "github_environment", "validation_environment"):
            val = need_str(env, key, where)
            if val and key in seen:
                if val in seen[key]:
                    errs.append(f"{where}.{key} {val!r} duplicates environments.{seen[key][val]}")
                seen[key][val] = name
        if isinstance(env.get("instance_url"), str) and not env["instance_url"].startswith("https://"):
            errs.append(f"{where}.instance_url must start with https://")
        if env.get("auth_method") not in AUTH_METHODS:
            errs.append(f"{where}.auth_method must be one of: {', '.join(AUTH_METHODS)}")
        for key in SECRET_KEYS:
            secret = need_str(env["secrets"], key, f"{where}.secrets")
            if secret and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", secret):
                errs.append(f"{where}.secrets.{key} {secret!r} is not a valid GitHub secret name")
        policy = env.get("test_policy")
        if policy not in TEST_POLICIES:
            errs.append(f"{where}.test_policy must be one of: {', '.join(TEST_POLICIES)}")
        approvals_n = env.get("required_approvals")
        if not is_int(approvals_n) or not 0 <= approvals_n <= 6:
            errs.append(f"{where}.required_approvals must be an integer 0-6")
        canary = env.get("canary_tests")
        if not isinstance(canary, list) or not all(isinstance(t, str) for t in canary):
            errs.append(f"{where}.canary_tests must be a list of test class names")
        elif policy == "canary" and not canary:
            errs.append(f"{where}.canary_tests needs at least one test class when test_policy is canary")
        if not isinstance(env.get("sync_after_release"), bool):
            errs.append(f"{where}.sync_after_release must be true or false")
        if not isinstance(env.get("jira_status"), str):
            errs.append(f"{where}.jira_status must be a string (the Jira status to move stories to)")
        if env.get("production"):
            if policy not in PRODUCTION_POLICIES:
                errs.append(f"{where}.test_policy cannot be 'none' for production")
            if baseline and env.get("branch") != baseline:
                errs.append(f"{where}.branch must equal project.baseline_branch ({baseline})")

    release = cfg["release"]
    source = release.get("source_env")
    if source not in envs or envs[source]["production"]:
        errs.append("release.source_env must name a non-production environment")
    need_str(release, "branch_prefix", "release")
    need_bool(release, "quick_deploy", "release")
    if not isinstance(release.get("tag_prefix"), str):
        errs.append("release.tag_prefix must be a string")

    prefixes = {
        "promotion.branch_prefix": promotion.get("branch_prefix"),
        "release.branch_prefix": release.get("branch_prefix"),
    }
    for label, prefix in prefixes.items():
        if isinstance(prefix, str):
            for name, env in envs.items():
                if str(env.get("branch", "")).startswith(prefix):
                    errs.append(f"environments.{name}.branch must not start with {label} ({prefix})")

    if repo_root and source_path:
        project_file = repo_root / "sfdx-project.json"
        if project_file.is_file():
            try:
                dirs = json.loads(project_file.read_text(encoding="utf-8")).get("packageDirectories", [])
                paths = {str(d.get("path", "")).replace("\\", "/").strip("/") for d in dirs}
            except (ValueError, AttributeError) as exc:
                errs.append(f"sfdx-project.json is not valid JSON: {exc}")
            else:
                if source_path.replace("\\", "/").strip("/") not in paths:
                    errs.append(f"project.source_path {source_path!r} is not a packageDirectories path in sfdx-project.json")
    return errs


# -------------------------------------------------------------------------- views


def _s(value: Any) -> str:
    """Render a value the way GitHub step outputs expect it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def production_env(cfg: dict) -> str:
    return next(k for k, v in cfg["environments"].items() if v.get("production"))


def globals_view(cfg: dict) -> dict[str, str]:
    project, github, approvals = cfg["project"], cfg["github"], cfg["approvals"]
    labels, promotion, release = github["labels"], cfg["promotion"], cfg["release"]
    envs = cfg["environments"]
    chat = cfg["notifications"]["chat"]
    return {
        "source_path": _s(project["source_path"]),
        "baseline_branch": _s(project["baseline_branch"]),
        "coverage_threshold": _s(cfg["quality"]["coverage_threshold"]),
        "require_companion": _s(cfg["tests"]["require_companion"]),
        "wait_minutes": _s(cfg["deploy"]["wait_minutes"]),
        "delta_output_dir": _s(cfg["deploy"]["delta_output_dir"]),
        "ignore_warnings": _s(cfg["deploy"]["ignore_warnings"]),
        "profiles_mode": _s(cfg["deploy"]["profiles_mode"]),
        "dependency_lint": _s(cfg["quality"]["dependency_lint"]),
        "bypass_label": _s(cfg["quality"]["bypass_label"]),
        "node_version": _s(cfg["tooling"]["node_version"]),
        "sf_cli_version": _s(cfg["tooling"]["sf_cli_version"]),
        "sgd_version": _s(cfg["tooling"]["sgd_version"]),
        "code_analyzer_version": _s(cfg["tooling"]["code_analyzer_version"]),
        "code_analyzer_enabled": _s(cfg["quality"]["code_analyzer"]["enabled"]),
        "code_analyzer_threshold": _s(cfg["quality"]["code_analyzer"]["severity_threshold"]),
        "code_analyzer_rules": _s(cfg["quality"]["code_analyzer"]["rule_selector"]),
        "pat_secret": _s(github["pat_secret"]),
        "admin_pat_secret": _s(github["admin_pat_secret"]),
        "status_check_app_id": _s(github["status_check_app_id"]),
        "promote_label_prefix": _s(labels["promote_prefix"]),
        "deploy_label_prefix": _s(labels["deploy_prefix"]),
        "approval_label": _s(approvals["label"]),
        "approval_status_check": _s(approvals["status_check"]),
        "dismiss_label_on_push": _s(approvals["dismiss_label_on_push"]),
        "approval_auto_merge": _s(approvals["auto_merge"]),
        "chain": _s(promotion["chain"]),
        "entry_env": _s(promotion["chain"][0]),
        "entry_branch": _s(envs[promotion["chain"][0]]["branch"]),
        "production_env": production_env(cfg),
        "promote_branch_prefix": _s(promotion["branch_prefix"]),
        "promotion_enforce_order": _s(promotion["enforce_order"]),
        "release_source_env": _s(release["source_env"]),
        "release_source_branch": _s(envs[release["source_env"]]["branch"]),
        "release_branch_prefix": _s(release["branch_prefix"]),
        "tag_prefix": _s(release["tag_prefix"]),
        "quick_deploy": _s(release["quick_deploy"]),
        "chat_enabled": _s(chat["enabled"]),
        "chat_provider": _s(chat["provider"]),
        "chat_webhook_secret": _s(chat["webhook_secret"]),
        "chat_api_url_secret": _s(chat["api_url_secret"]),
        "chat_api_token_secret": _s(chat["api_token_secret"]),
        "chat_channel_secret": _s(chat["channel_secret"]),
        "chat_deploy_success": _s(chat["deploy_success"]),
        "jira_enabled": _s(cfg["notifications"]["jira"]["enabled"]),
        "jira_base_url_secret": _s(cfg["notifications"]["jira"]["base_url_secret"]),
        "jira_email_secret": _s(cfg["notifications"]["jira"]["email_secret"]),
        "jira_token_secret": _s(cfg["notifications"]["jira"]["token_secret"]),
        "audit_enabled": _s(cfg["audit"]["enabled"]),
        "audit_branch": _s(cfg["audit"]["branch"]),
    }


def env_view(cfg: dict, name: str) -> dict[str, str]:
    env = cfg["environments"][name]
    chain = cfg["promotion"]["chain"]
    idx = chain.index(name)
    view = globals_view(cfg)
    view.update(
        {
            "env": name,
            "branch": _s(env["branch"]),
            "org_alias": _s(env["org_alias"]),
            "instance_url": _s(env["instance_url"]),
            "auth_method": _s(env["auth_method"]),
            "auth_url_secret": _s(env["secrets"]["auth_url"]),
            "username_secret": _s(env["secrets"]["username"]),
            "consumer_key_secret": _s(env["secrets"]["consumer_key"]),
            "jwt_key_secret": _s(env["secrets"]["jwt_key"]),
            "status_check": _s(env["status_check"]),
            "test_policy": _s(env["test_policy"]),
            "canary_tests": _s(env["canary_tests"]),
            "required_approvals": _s(env["required_approvals"]),
            "github_environment": _s(env["github_environment"]),
            "validation_environment": _s(env["validation_environment"]),
            "is_production": _s(env["production"]),
            "is_entry": _s(idx == 0),
            "sync_after_release": _s(env["sync_after_release"]),
            "jira_status": _s(env["jira_status"]),
            "chain_index": _s(idx),
            "previous_env": _s(chain[idx - 1] if idx > 0 else ""),
            "next_env": _s(chain[idx + 1] if idx + 1 < len(chain) else ""),
        }
    )
    return view


def required_secrets(cfg: dict, name: str) -> list[str]:
    """Secret names an environment needs for its auth method."""
    env = cfg["environments"][name]
    if env["auth_method"] == "sfdx_url":
        return [env["secrets"]["auth_url"]]
    return [env["secrets"]["username"], env["secrets"]["consumer_key"], env["secrets"]["jwt_key"]]


def find_env(
    cfg: dict,
    *,
    name: str | None = None,
    branch: str | None = None,
    label: str | None = None,
    production: bool = False,
) -> tuple[str | None, str]:
    """Return (environment key or None, how it matched)."""
    envs = cfg["environments"]
    if production:
        return production_env(cfg), "production"
    if name:
        return (name if name in envs else None), "name"
    if branch:
        branch = branch.removeprefix("refs/heads/")
        return next((k for k, v in envs.items() if v["branch"] == branch), None), "branch"
    if label:
        labels = cfg["github"]["labels"]
        for kind in ("deploy", "promote"):
            prefix = labels[f"{kind}_prefix"]
            if label.startswith(prefix):
                candidate = label[len(prefix):].strip()
                return (candidate if candidate in envs else None), kind
    return None, ""


def promote_plan(cfg: dict, base_branch: str, labels: list[str] | None = None, target: str | None = None) -> dict:
    """Work out which environments a merged PR should be promoted into."""
    envs = cfg["environments"]
    chain = cfg["promotion"]["chain"]
    prefix = cfg["github"]["labels"]["promote_prefix"]
    enforce = cfg["promotion"]["enforce_order"]
    base_env, _ = find_env(cfg, branch=base_branch)

    requested = [target] if target else [lbl[len(prefix):].strip() for lbl in labels or [] if lbl.startswith(prefix)]
    targets: list[dict[str, str]] = []
    errors: list[str] = []
    if requested and base_env is None:
        errors.append(f"base branch {base_branch!r} is not an environment branch; nothing to promote from")
        requested = []
    for env in dict.fromkeys(requested):
        if env not in envs:
            errors.append(f"unknown environment {env!r} (known: {', '.join(chain)})")
        elif envs[env]["production"]:
            errors.append(f"{env} is production; run the SF Prod Release workflow instead")
        elif env == base_env:
            errors.append(f"the PR is already in {env}")
        elif enforce and chain.index(env) != chain.index(base_env) + 1:
            nxt = chain[chain.index(base_env) + 1] if chain.index(base_env) + 1 < len(chain) else None
            if nxt is None or envs[nxt]["production"]:
                errors.append(f"{base_env} is promoted to production by the SF Prod Release workflow, not by a label")
            else:
                errors.append(f"promotion order is enforced: {base_env} can only promote to {nxt}")
        else:
            targets.append({"env": env, "branch": envs[env]["branch"]})
    return {"base_env": base_env or "", "targets": targets, "errors": errors}


# --------------------------------------------------------------------------- output


def write_outputs(values: dict[str, str], github_output: bool) -> None:
    lines = [f"{key}={str(value).replace(chr(13), ' ').replace(chr(10), ' ')}" for key, value in values.items()]
    if github_output:
        target = os.environ.get("GITHUB_OUTPUT")
        if not target:
            raise ConfigError("--github-output used but GITHUB_OUTPUT is not set")
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def _get(cfg: dict, dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"key not found: {dotted}")
        node = node[part]
    return node


# ----------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="Path to pipeline.config.yml (default: sf-pipeline/pipeline.config.yml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate the config")
    sub.add_parser("show", help="print the fully-resolved config")

    p_env = sub.add_parser("env", help="resolve one environment")
    sel = p_env.add_mutually_exclusive_group()
    sel.add_argument("--name")
    sel.add_argument("--branch")
    sel.add_argument("--label")
    sel.add_argument("--production", action="store_true")
    p_env.add_argument("--github-output", action="store_true")

    p_envs = sub.add_parser("envs", help="JSON list of environment keys")
    p_envs.add_argument("--sync-only", action="store_true", help="only non-production envs with sync_after_release")

    p_promo = sub.add_parser("promote-plan", help="resolve promotion targets")
    p_promo.add_argument("--base-branch", required=True)
    p_promo.add_argument("--labels", default="", help="comma-separated PR labels")
    p_promo.add_argument("--target", default="")
    p_promo.add_argument("--github-output", action="store_true")
    p_promo.add_argument("--fail-on-error", action="store_true", help="annotate errors and exit 1")

    p_get = sub.add_parser("get", help="print one resolved value")
    p_get.add_argument("key")

    args = parser.parse_args(argv)
    try:
        cfg = load(args.config)

        if args.command == "validate":
            chain = cfg["promotion"]["chain"]
            print(f"pipeline config OK: {len(chain)} environments: {' -> '.join(chain)} (production: {production_env(cfg)})")
            for name in chain:
                env = cfg["environments"][name]
                print(
                    f"  {name:<10} branch={env['branch']:<10} auth={env['auth_method']:<8} "
                    f"secrets={', '.join(required_secrets(cfg, name))}"
                )
            return 0

        if args.command == "show":
            print(yaml.safe_dump(cfg, sort_keys=False))
            return 0

        if args.command == "env":
            name, matched_by = find_env(
                cfg, name=args.name, branch=args.branch, label=args.label, production=args.production
            )
            values = env_view(cfg, name) if name else globals_view(cfg)
            values["matched_by"] = matched_by if name else ""
            values["found"] = _s(name is not None)
            if not args.github_output:
                print(json.dumps(values, indent=2))
                return 0
            values["json"] = json.dumps(values, separators=(",", ":"))
            write_outputs(values, True)
            return 0

        if args.command == "envs":
            envs = cfg["environments"]
            keys = [k for k in cfg["promotion"]["chain"] if not args.sync_only or envs[k]["sync_after_release"]]
            print(json.dumps(keys, separators=(",", ":")))
            return 0

        if args.command == "promote-plan":
            labels = [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()]
            plan = promote_plan(cfg, args.base_branch, labels, args.target or None)
            if args.github_output:
                write_outputs(
                    {
                        "base_env": plan["base_env"],
                        "targets": json.dumps(plan["targets"], separators=(",", ":")),
                        "count": str(len(plan["targets"])),
                        "errors": json.dumps(plan["errors"], separators=(",", ":")),
                    },
                    True,
                )
            else:
                print(json.dumps(plan, indent=2))
            if args.fail_on_error and plan["errors"]:
                for err in plan["errors"]:
                    print(f"::error title=Promotion::{err}")
                return 1
            return 0

        if args.command == "get":
            value = _get(cfg, args.key)
            print(value if isinstance(value, (str, int, float)) and not isinstance(value, bool) else json.dumps(value))
            return 0
    except ConfigError as exc:
        for line in str(exc).splitlines():
            print(f"::error title=pipeline.config.yml::{line.strip()}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())

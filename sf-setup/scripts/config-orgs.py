#!/usr/bin/env python3
"""
config-orgs.py — derive per-org bootstrap rows from a project's
sf-pipeline/pipeline.config.yml, so the ACTUAL sandbox values live in exactly one
place. The pipeline stage names + branches stay identical across projects; only
each environment's `sandbox:` name (and the `org:` templates) change per project.

Output: TSV rows  ->  env<TAB>alias<TAB>instance_url<TAB>username<TAB>sandbox
Includes every promotion-chain env plus production's deploy_environment and
validate_environment (both mapped to the production org).

Usage: config-orgs.py [path/to/pipeline.config.yml]
"""
import sys

try:
    import yaml
except ImportError:
    import os
    os.system(f"{sys.executable} -m pip install --quiet pyyaml")
    import yaml


def render(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "sf-pipeline/pipeline.config.yml"
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    org = cfg.get("org", {}) or {}
    # `prod_org` is the single per-project variable (the production My Domain /
    # org name). `my_domain` is accepted as a backward-compatible alias.
    prod_org = org.get("prod_org") or org.get("my_domain", "")
    sb_tpl = org.get("sandbox_url_template",
                     "https://{prod_org}--{sandbox}.sandbox.my.salesforce.com")
    prod_url = render(org.get("prod_url", "https://{prod_org}.my.salesforce.com"),
                      prod_org=prod_org, my_domain=prod_org)
    # Integration user base defaults from the org name if not given explicitly.
    user_base = org.get("username_base") or (f"svc_cicd@{prod_org}.com" if prod_org else "")

    def sandbox_url(sb):
        return render(sb_tpl, prod_org=prod_org, my_domain=prod_org, sandbox=sb)

    def sandbox_user(sb):
        return f"{user_base}.{sb}" if user_base and sb else user_base

    rows = []
    for e in cfg.get("environments", []) or []:
        # Sandbox name defaults to the pipeline stage name (dev/int/qa/stage),
        # so most projects never set `sandbox:` at all.
        sb = e.get("sandbox") or e["name"]
        url = e.get("instance_url") or sandbox_url(sb)
        user = e.get("integration_username") or sandbox_user(sb)
        rows.append((e.get("gh_environment", e["name"]), f"{e['name']}-admin", url, user, sb))

    prod = cfg.get("production") or {}
    if prod:
        sb = prod.get("sandbox") or "prod"
        is_sb = bool(prod.get("is_sandbox", False))
        if prod.get("instance_url"):
            url = prod["instance_url"]
        elif is_sb and sb:
            url = sandbox_url(sb)
        else:
            url = prod_url
        if prod.get("integration_username"):
            user = prod["integration_username"]
        elif is_sb and sb:
            user = sandbox_user(sb)
        else:
            user = user_base
        alias = "prod-admin"
        dep = prod.get("deploy_environment", "prod")
        val = prod.get("validate_environment")
        rows.append((dep, alias, url, user, sb))
        if val:
            rows.append((val, alias, url, user, sb))

    for r in rows:
        print("\t".join(str(x) for x in r))


if __name__ == "__main__":
    main()

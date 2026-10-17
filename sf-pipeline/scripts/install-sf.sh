#!/usr/bin/env bash
#
# install-sf.sh — install the Salesforce CLI + sfdx-git-delta deterministically in CI.
#
# Why this exists: `echo y | sf plugins install ...` is flaky on CI runners. Piping
# stdin into the CLI closes stdin for the npm child process it shells out to, which
# surfaces as `ShellExecError`. Instead we pre-approve the unsigned plugin via the
# allowlist file so the install is fully non-interactive.
#
set -euo pipefail

CLI_VERSION="${SF_CLI_VERSION:-2.120.3}"
SGD_VERSION="${SGD_VERSION:-6}"

echo "::group::Install @salesforce/cli@${CLI_VERSION}"
npm install --global "@salesforce/cli@${CLI_VERSION}"
echo "::endgroup::"

export SF_DISABLE_TELEMETRY=true
export SFDX_DISABLE_TELEMETRY=true

# Warm up so first-run notices are printed before the plugin install.
sf --version >/dev/null 2>&1 || true

# Pre-approve the unsigned sfdx-git-delta plugin across all candidate config dirs.
for d in "$HOME/.config/sfdx" "$HOME/.config/sf" "$HOME/.sfdx" "$HOME/.sf"; do
  mkdir -p "$d"
  printf '["sfdx-git-delta"]\n' > "$d/unsignedPluginAllowList.json"
done

echo "::group::Install sfdx-git-delta@${SGD_VERSION}"
sf plugins install "sfdx-git-delta@${SGD_VERSION}"
echo "::endgroup::"

sf version --verbose --json || true
sf plugins --core || true

#!/usr/bin/env bash
# Runs INSIDE the SIF. scitex-dev ecosystem audits for scitex-todo.
#
# scitex-dev[all,dev] (which includes the audit tooling) is BAKED in the SIF, so
# the `scitex-dev` console script + the ecosystem audit commands resolve from
# the baked venv directly — no per-run `pip install scitex-dev[cli-audit]`.
# todo's own package is installed editable into a writable target so audits that
# import it (e.g. MCP-tool / python-API parity) see the checkout's code.
#
# Scope-down preserved from todo's develop workflow (PR #110 fix-forward):
# run only the audit categories whose drift is fixable in a focused PR; treat
# audit-skills as warn-only until the SK-301/401 cleanup lands.
set -euo pipefail

V="${1:-3.12}"
VENV="/opt/venv-$V"
test -x "$VENV/bin/scitex-dev" || {
    echo "::error::baked scitex-dev console script missing in $VENV — rebuild the SIF: scitex-container apptainer build ci-cpu"
    exit 1
}

export LC_ALL=C.UTF-8 LANG=C.UTF-8
export TMPDIR="/tmp/ci-todo-audit-$V"
rm -rf "$TMPDIR"
mkdir -p "$TMPDIR/site" "$TMPDIR/uv-cache"
export UV_CACHE_DIR="$TMPDIR/uv-cache"
export XDG_CACHE_HOME="$TMPDIR"
export PIP_CACHE_DIR="$TMPDIR/pip-cache"
unset VIRTUAL_ENV || true
export PATH="$VENV/bin:$PATH"

# Install todo + extras into a writable target so audits importing the package
# resolve the checkout's code. --no-deps: scitex-dev (the audit driver) is baked;
# the audits only need todo's OWN modules importable, not its full dep tree.
uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" --no-deps -e ".[mcp]" ||
    uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" --no-deps -e "."
export PYTHONPATH="$TMPDIR/site:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

echo "scitex-dev=$(command -v scitex-dev) ver=$(scitex-dev --version 2>&1 | head -1)"

# --path <abs> audits THIS checkout; without it audit-project resolves the
# ecosystem-registry local_path (the runner's stale ~/proj checkout) and
# spuriously fails PS-101/133/134. MUST be ABSOLUTE.
WS="${GITHUB_WORKSPACE:-$PWD}"

scitex-dev ecosystem audit-cli scitex-todo
scitex-dev ecosystem audit-mcp-tools scitex-todo
# audit-skills returns rc=1 even when every finding is WARN; echo + continue.
scitex-dev ecosystem audit-skills scitex-todo || true
scitex-dev ecosystem audit-project scitex-todo --path "$WS"

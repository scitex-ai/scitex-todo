#!/usr/bin/env bash
# Runs INSIDE the SIF. Import-smoke: bare (no-extras) install + import + --help.
# $1 = python version (default 3.12).
#
# Mirrors todo's historical import-smoke job, but inside the reused SIF instead
# of a per-run setup-uv extraction. Bare install (no [all,dev]) so we catch a
# package that only imports when an optional extra happens to be present.
set -euo pipefail

V="${1:-3.12}"
VENV="/opt/venv-$V"
test -x "$VENV/bin/python" || {
    echo "::error::baked python missing in $VENV — rebuild the SIF: scitex-container apptainer build ci-cpu"
    exit 1
}

export LC_ALL=C.UTF-8 LANG=C.UTF-8
export TMPDIR="/tmp/ci-todo-smoke-$V"
rm -rf "$TMPDIR"
mkdir -p "$TMPDIR/site" "$TMPDIR/uv-cache"
export UV_CACHE_DIR="$TMPDIR/uv-cache"
export XDG_CACHE_HOME="$TMPDIR"
export PIP_CACHE_DIR="$TMPDIR/pip-cache"
unset VIRTUAL_ENV || true
export PATH="$VENV/bin:$PATH"

# Bare install (no extras) into the writable target.
uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" -e "."

export PYTHONPATH="$TMPDIR/site:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

echo "===import==="
python -c "import scitex_todo; print(getattr(scitex_todo, '__version__', 'unversioned'))"
echo "===scitex-todo --help==="
python -m scitex_todo --help >/dev/null && echo "scitex-todo --help OK"

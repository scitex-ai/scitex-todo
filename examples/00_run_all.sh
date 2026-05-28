#!/bin/bash
# ./examples/00_run_all.sh — run every numbered example end to end.
set -euo pipefail
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$THIS_DIR"
for f in 0[1-9]_*.py 1[0-9]_*.py; do
    [ -f "$f" ] || continue
    echo "==> $f"
    python "$f"
done

# EOF

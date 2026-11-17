#!/usr/bin/env bash
set -euo pipefail

tf_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$tf_dir")"
python="${PYTHON:-python3.12}"

"$python" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Build with Python 3.12"'
mkdir -p "$tf_dir/.build/scheduler"
"$python" -m pip install \
  --requirement "$project_dir/src/requirements.txt" \
  --target "$tf_dir/.build/scheduler" \
  --upgrade --no-compile
cp "$project_dir"/src/*.py "$tf_dir/.build/scheduler/"
printf 'Lambda source and dependencies prepared in %s/.build/scheduler\n' "$tf_dir"

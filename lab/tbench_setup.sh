#!/bin/sh
# One-time setup for the Terminal-Bench external check (macOS/Linux arm64 host with Docker).
set -eu
LAB=$(cd "$(dirname "$0")/.." && pwd); BASE=$(dirname "$LAB")
COMMIT=$(python3 -c "import json;print(json.load(open('$LAB/docs/terminal-bench-preregistration.json'))['commit'])")
[ -d "$BASE/tbench" ] || git clone -q https://github.com/laude-institute/terminal-bench.git "$BASE/tbench"
git -C "$BASE/tbench" fetch -q --depth 1 origin "$COMMIT" && git -C "$BASE/tbench" checkout -q "$COMMIT"
mkdir -p "$BASE/tb-wheels" "$BASE/tb-py"
python3 -m pip download deepseek-harness-sdk --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64 \
  --only-binary=:all: --python-version 3.12 -d "$BASE/tb-wheels" -q
URL=$(curl -s https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest | python3 -c "import json,sys;print([a['browser_download_url'] for a in json.load(sys.stdin)['assets'] if a['name'].startswith('cpython-3.12') and a['name'].endswith('aarch64-unknown-linux-gnu-install_only.tar.gz')][0])")
curl -sL "$URL" | tar -xz -C "$BASE/tb-py"
docker run --rm -v "$BASE/tb-py:/opt/dshpy" -v "$BASE/tb-wheels:/wheels:ro" python:3.12-slim \
  /opt/dshpy/python/bin/python3 -m pip install -q --no-index --find-links /wheels deepseek-harness-sdk
echo "ready"

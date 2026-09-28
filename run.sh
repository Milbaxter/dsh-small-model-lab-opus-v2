#!/bin/sh
# One command: run a prompt in a workspace with DSH standard (`sdk` profile) + the packaged bundle.
#   OPENAI_COMPAT_BASE_URL=... OPENAI_COMPAT_API_KEY=... MODEL=qwen/qwen3-8b ./run.sh /path/to/workspace "prompt"
# Requires: python3 with `pip install deepseek-harness-sdk pyyaml`.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
WS=$(cd "$1" && pwd); PROMPT=$2
BUNDLE=${BUNDLE:-$HERE/bundle/dsh-lab-opus-stop-guard}
export LAB_BASE_URL=${OPENAI_COMPAT_BASE_URL:?set OPENAI_COMPAT_BASE_URL} LAB_API_KEY=${OPENAI_COMPAT_API_KEY:?set OPENAI_COMPAT_API_KEY}
export DSH_TELEMETRY_MODE=DISABLED DSH_PERMISSION_MODE=danger-full-access
HOME_DIR=$(mktemp -d)
python3 - "$WS" "$PROMPT" "$HOME_DIR" "$BUNDLE" "$HERE" "${MODEL:-qwen/qwen3-8b}" <<'PY'
import sys, pathlib
from deepseek_harness import DeepSeekHarness
ws, prompt, home, bundle, here, model = sys.argv[1:]
arms = pathlib.Path(here) / "harness" / "arms"
prov = (arms / "lab-provider.patch.yml").read_text().replace("qwen/qwen3-8b", model)
p = pathlib.Path(home) / "provider.patch.yml"; p.write_text(prov)
patches = (str(arms / "standard.patch.yml"), str(pathlib.Path(bundle) / "direct.patch.yml"), str(p))
with DeepSeekHarness(dsh_home=home, cwd=ws, provider="lab", model=model, profile="sdk", patches=patches) as h:
    print(h.run(prompt, session_id="run-1").final_response)
PY

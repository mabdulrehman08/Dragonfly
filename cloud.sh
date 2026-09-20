#!/usr/bin/env bash
# One-shot setup + run inside an XO cloud space (app.xo.builders, Claude Code template).
# Run from the folder you cloned Dragonfly into, in the code-server terminal:
#     bash cloud.sh
# Then open the forwarded port 8916 from the code-server Ports panel.
set -Eeuo pipefail
cd "$(dirname "$0")"

# The pinned pydantic needs Python 3.12 (cloud images ship newer). uv fetches a managed 3.12 without root.
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
    rm -rf .venv
    command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
    uv venv --python 3.12 .venv
fi
export PATH="$HOME/.local/bin:$PATH"
uv pip install --quiet --python .venv/bin/python -r requirements.txt

command -v claude >/dev/null || { echo "claude CLI not found: connect Claude in Setup → Models, or npm i -g @anthropic-ai/claude-code"; exit 1; }
claude -p 'reply with the word ok' --max-turns 1 --output-format json >/dev/null 2>&1 || echo "warning: claude CLI is not authenticated in this shell (Setup → Models → Connect Claude, or run: claude login)"
export DRAGONFLY_LLM=${DRAGONFLY_LLM:-claude-code}
export DRAGONFLY_XO_REGISTER=${DRAGONFLY_XO_REGISTER:-1}
echo "Dragonfly backend on http://127.0.0.1:8916  (mode: $DRAGONFLY_LLM)"
exec .venv/bin/uvicorn dragonfly.server:app --host 0.0.0.0 --port 8916

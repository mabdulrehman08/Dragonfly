#!/usr/bin/env bash
# One-shot setup + run inside an XO cloud space (app.xo.builders, Claude Code template).
# Run from the folder you cloned Dragonfly into, in the code-server terminal:
#     bash cloud.sh
# Then open the forwarded port 8916 from the code-server Ports panel.
set -Eeuo pipefail
cd "$(dirname "$0")"
PY=$(command -v python3.12 || command -v python3)
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt
command -v claude >/dev/null || { echo "claude CLI not found: connect Claude in Setup → Models, or npm i -g @anthropic-ai/claude-code"; exit 1; }
claude -p 'reply with the word ok' --max-turns 1 --output-format json >/dev/null 2>&1 || echo "warning: claude CLI is not authenticated in this shell (Setup → Models → Connect Claude, or run: claude login)"
export DRAGONFLY_LLM=${DRAGONFLY_LLM:-claude-code}
export DRAGONFLY_XO_REGISTER=${DRAGONFLY_XO_REGISTER:-1}
echo "Dragonfly backend on http://127.0.0.1:8916  (mode: $DRAGONFLY_LLM)"
exec .venv/bin/uvicorn dragonfly.server:app --host 0.0.0.0 --port 8916

"""Optional XO Space (Quirq) registration for `claude-code` runs.

XO Space attributes a Claude Code session to a project only when a row for it exists in the Space's own index at
`<QUIRQ_STATE_ROOT>/projects/<pid>/sessions/sessionslist.d/`. The Space writes that row itself for agents it
launches; for agents we launch, we write the same row. This is observability bookkeeping in Quirq's state
directory, never in the project tree, and it is off unless `NINESIXTEEN_XO_REGISTER=1` and the repo has been
adopted by a Space (`.xo/project.json` exists). Format mirrors xo-space `adapters/claude_code/adapter.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def enabled() -> bool:
    return os.environ.get("NINESIXTEEN_XO_REGISTER", "") == "1" and (REPO_ROOT / ".xo" / "project.json").exists()


def _shard_dir() -> Path | None:
    try:
        pid = json.loads((REPO_ROOT / ".xo" / "project.json").read_text()).get("pid")
    except (OSError, ValueError):
        return None
    if not pid:
        return None
    state = Path(os.environ.get("QUIRQ_STATE_ROOT", Path.home() / ".quirq"))
    return state / "projects" / str(pid) / "sessions" / "sessionslist.d"


def register(native_session_id: str, usage: dict[str, int] | None = None) -> None:
    """Write (or refresh) the index row for one run. Silent on any failure: observability never blocks the agent."""
    shard_dir = _shard_dir()
    if shard_dir is None:
        return
    key = f"claude:{REPO_ROOT.name}:web:{native_session_id[:8]}"
    row = {
        "sessionId": native_session_id,
        "nativeSessionId": native_session_id,
        "directory": str(REPO_ROOT),
        "backend": "claude_code",
        "updatedAt": int(time.time() * 1000),
        "usage": usage or {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }
    try:
        shard_dir.mkdir(parents=True, exist_ok=True)
        path = shard_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"
        tmp = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps({key: row}))
        tmp.replace(path)
    except OSError:
        return

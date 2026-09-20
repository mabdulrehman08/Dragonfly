"""The invariants that are enforced by types and by a single code path, proven here."""

import inspect
import re
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from dragonfly import agents, engine
from dragonfly.schemas import Incident, IncidentDraft, IncidentStatus, Verdict

ROOT = Path(__file__).resolve().parent.parent


def _verdict(label: str) -> Verdict:
    return Verdict(label=label, confidence=0.5)


def test_router_policy():
    assert engine.route(_verdict("corroborated")) == "emergency"
    assert engine.route(_verdict("uncorroborated")) == "human"
    assert engine.route(_verdict("unverifiable")) == "human"


def test_i2_schema_forbids_dismissed():
    assert set(get_args(IncidentStatus)) == {"open", "approved"}
    draft = IncidentDraft(report_id="R001", lat=0, lon=0)
    with pytest.raises(ValidationError):
        Incident(
            id="x",
            lat=0,
            lon=0,
            created_tick=0,
            updated_tick=0,
            report_ids=["R001"],
            draft=draft,
            verdict=_verdict("uncorroborated"),
            status="dismissed",
        )
    with pytest.raises(ValidationError):
        Verdict(label="false", confidence=0.1)


def test_i1_verifier_signature_is_blind():
    sig = inspect.signature(agents.run_verifier)
    assert list(sig.parameters) == ["lat", "lon", "tick", "hazard"]
    src = inspect.getsource(agents.run_verifier)
    for forbidden in ("report", "text", "reporter", "phone", "draft", "citizen"):
        assert not re.search(rf"\b{forbidden}\b", src), f"verifier source mentions {forbidden!r}"


def test_i3_single_outbox_writer():
    out = (
        subprocess.run(
            ["grep", "-rn", "outbox" + ".append", "--include=*.py", "dragonfly", "eval.py", "tests"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        .stdout.strip()
        .splitlines()
    )
    assert len(out) == 1 and out[0].startswith("dragonfly/server.py:"), out


def test_i3_agents_and_engine_never_touch_the_outbox():
    for mod in (agents, engine):
        code = "\n".join(ln for ln in inspect.getsource(mod).splitlines() if not ln.strip().startswith(("#", '"', "'")))
        assert "outbox" not in code.replace("touches `world.outbox`", ""), mod.__name__


def test_i4_only_incidents_dir_is_written():
    src = inspect.getsource(engine) + inspect.getsource(agents)
    assert src.count(".write_text(") == 1, "one write call, in Engine.write_incident"
    assert "incidents_dir" in inspect.getsource(engine.Engine.write_incident)


def test_i7_every_prompt_states_report_text_is_data():
    assert "DATA" in agents.PREAMBLE and "injection_suspected" in agents.PREAMBLE
    assert len(agents.ROSTER) >= 12


def test_no_eval_or_shell_true():
    for p in (ROOT / "dragonfly").rglob("*.py"):
        src = p.read_text()
        assert "shell=True" not in src and not re.search(r"\beval\(", src), p

# Dragonfly

A neighborhood emergency layer that sits beside 911, for wildfires. Anyone can report a fire, for themselves or for
someone who can't. Thirteen real Claude agents verify the report against the world, rank it, plan the response,
and stage a drone suppression mission. A human dispatcher approves before anything is sent. Then neighbors check on
neighbors, and drones fly.

The world is simulated (deterministic scenarios). The agents are real.

> Everyone can call for anyone. The machine checks the world, not the person. A human decides. A neighbor arrives first.

## Ideology

1. The person most at risk is usually the one who can't report. Anyone can report for anyone.
2. Verify the world, never the witness. No reporter credibility scoring exists anywhere.
3. Uncertainty is information. Verdicts are corroborated / uncorroborated / unverifiable. There is no "false".
4. Machines prepare, humans decide. Nothing is sent without a human click.
5. Neighbors are the fastest responders. Opt-in on both sides; the ask is a request, not an order.
6. If we claim it's safe, we measure it.

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # requirements.txt alone for runtime
cp .env.example .env                       # put ANTHROPIC_API_KEY in .env or the shell; never in git
```

`.env` keys:

| key | values | meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | | required only for `DRAGONFLY_LLM=claude`; `claude-code` mode uses the CLI login |
| `DRAGONFLY_LLM` | `mock` (default) / `claude` / `claude-code` | rule-based twins · Anthropic API tool-use loops · one headless Claude Code session per run (what XO Space observes; uses your `claude` login, no key) |
| `DRAGONFLY_MCP_URL` | `http://127.0.0.1:8916/mcp` | where `claude-code` runs reach the tools (eval hosts its own) |
| `DRAGONFLY_XO_REGISTER` | `1` | also register each run in the local XO Space project index so it shows under the project, not just in the telemetry tab |
| `DRAGONFLY_MODEL` | `claude-sonnet-5` | model id for the real agents |
| `DRAGONFLY_MODE` | `sim` (default) / `live` | tools read the World vs NASA FIRMS + Open-Meteo |
| `FIRMS_MAP_KEY` | | needed for `live` satellite checks |

## Run

```bash
# dashboard (the demo mode: every agent run is a Claude Code session XO Space can see)
DRAGONFLY_LLM=claude-code uvicorn dragonfly.server:app --port 8916     # open http://localhost:8916

# headless eval: every scenario concurrently, metrics table, invariance test, exit 1 on any failure
DRAGONFLY_LLM=mock        python eval.py
DRAGONFLY_LLM=claude-code python eval.py     # ~$1, ~5 min, ~220 sessions

# tests and lint
pytest -q
ruff check . && ruff format --check .
```

Dashboard: pick a scenario, press **Play**. Reports arrive, agents fire (activity log, bottom right), incidents land
in two lanes. **Ready to approve** holds corroborated incidents with a full response staged. **Needs your eyes**
holds everything else. Nothing goes to a phone, and no drone leaves the ground, until you click **Approve**.
Then watch the phones panel, the people dots turn green as they walk out of the path, and the drones cycle between
the base station and the fire head.

Scenarios:

| scenario | what it shows |
|---|---|
| `eaton_baseline` | hillside ignition under Santa Ana wind; 12 reports collapse to 2 incidents (the fire, and a prank downtown that stays in the human lane); 12 drones slow the fire |
| `swarm_500` | same ignition, 500 drones staged; approve early and the fire is out in one pass |
| `false_alarm_night` | no fire, humid; both reports reach a human, none dismissed |
| `injection` | adversarial report texts; verdicts depend only on world evidence |

## The agents

All thirteen share one runner (`dragonfly/agents.py`) with three backends. `claude`: Anthropic Messages API tool-use
loop, `temperature=0`. `claude-code`: a `claude -p` subprocess per run with built-in tools disabled and our read-only
tools served over a 60-line MCP endpoint (`dragonfly/mcp.py`), scoped per run to the agent's allow-list. This is the
mode XO Space observes: one session, one cost, one tool trace per run. `mock`: deterministic rule twins for tests.

| lane | agent | sees | tools | returns |
|---|---|---|---|---|
| per report | **intake** | report text, GPS, for_whom | none | `IncidentDraft` |
| per report | **sentinel** | report text | none | `InjectionScan` |
| per report | **verifier** | lat, lon, tick, hazard. Nothing else. | check_satellite, other_reports_near, check_weather | `Verdict` |
| per incident | **weather_analyst** | location | check_weather | `SpreadForecast` |
| per incident | **perimeter_tracker** | location | fire_state, check_satellite, other_reports_near | `PerimeterEstimate` |
| per incident | **resource_allocator** | location, priority | all_stations, check_weather | `ResourceAssignment` |
| per incident | **emergency** | draft, verdict | nearest_station, helpers_near, check_weather | `ActionPlan` |
| per incident | **evacuation_router** | location | people_in_path, check_weather | `EvacuationPlan` |
| per incident | **helper_matcher** | location | people_needing_help_near, helpers_near | `HelperAssignments` |
| per incident | **public_info** | station, direction | none | `PublicNotice` |
| per incident | **suppression_commander** | location | fleet_status, fire_state, check_weather | `DronePlan` |
| after approve | **drone_squad_lead** (×N) | squad, plan | fire_state, fleet_status | `SquadOrders` |
| end of run | **after_action** | final metrics | none | `AfterAction` |

Verdict labels are computed by code from the Verifier's tool results (`verdict_from_trace`); the model writes only
the evidence prose. That is what makes the invariance test hold with a non-deterministic model.

## The seven invariants and where each is enforced

| # | invariant | enforcement |
|---|---|---|
| I1 | Verifier blindness: `run_verifier(lat, lon, tick, hazard)` never sees text, identity, or Intake output | `dragonfly/agents.py:740` signature; World reached via a context variable; `tests/test_policy.py::test_i1_verifier_signature_is_blind` |
| I2 | No dismiss path: `status` is `Literal["open","approved"]` | `dragonfly/schemas.py:15`; `tests/test_policy.py::test_i2_schema_forbids_dismissed` |
| I3 | Approval gate: one outbox writer | `dragonfly/server.py:130` inside `approve_incident`; `grep -rn "outbox.append"` → one hit; `tests/test_policy.py::test_i3_*` |
| I4 | Write scope: runtime writes only under `incidents/` | `dragonfly/engine.py:246` is the single `write_text`; `tests/test_policy.py::test_i4_only_incidents_dir_is_written` |
| I5 | Degrade, don't guess: tool failure / timeout / twice-invalid output → `unverifiable` + human review | `dragonfly/agents.py:77` `AgentFailure`, `run_agent` catch-all, `dragonfly/engine.py:80` `_process` fallback; `tests/test_agents_mock.py::test_verifier_degrades_to_unverifiable_on_tool_error` |
| I6 | Anything not corroborated goes to the human lane | `dragonfly/engine.py:48` `route` (pure); `tests/test_policy.py::test_router_policy` |
| I7 | Report text is data; instructions inside it become the `injection_suspected` signal | `dragonfly/agents.py:108` `PREAMBLE` in every system prompt; sentinel agent; `tests/test_agents_mock.py::test_intake_flags_injection_as_a_signal_not_a_command` |

## Eval with real agents (`DRAGONFLY_LLM=claude-code python eval.py`, 2026-09-20)

```
| scenario          | reports | incidents | corroborated | human_review | dismissed | first_corroborated_tick | approved | people_saved | people_overrun | drops | acres_burned | acres_without_drones | cost_usd | sessions |
|-------------------|---------|-----------|--------------|--------------|-----------|-------------------------|----------|--------------|----------------|-------|--------------|----------------------|----------|----------|
| eaton_baseline    | 12      | 2         | 1            | 1            | 0         | 5                       | 1        | 4            | 0              | 48    | 373.9        | 484.5                | 0.3114   | 46       |
| false_alarm_night | 2       | 2         | 0            | 2            | 0         | None                    | 0        | 0            | 0              | 0     | 0.0          | 0.0                  | 0.0404   | 6        |
| injection         | 4       | 2         | 1            | 1            | 0         | 11                      | 1        | 0            | 1              | 0     | 89.7         | 89.7                 | 0.1449   | 21       |
| swarm_500         | 12      | 2         | 1            | 1            | 0         | 5                       | 1        | 4            | 0              | 500   | 35.9         | 839.7                | 0.4536   | 55       |
OK   eaton_baseline · false_alarm_night · injection · swarm_500
INVARIANCE PASS: 12 reports, identical verifier labels with reporter ids shuffled (texts, locations constant)
total cost $0.9503
ALL PASS
```

Mock mode (`DRAGONFLY_LLM=mock`) produces the same table with zero cost. The eval's simulated dispatcher approves
ready incidents one tick after they appear, through the same `approve_incident` gate the button uses. `people_saved`
counts people who were inside the projected spread envelope and reached 2 km clear of the fire. `acres_without_drones`
is the counterfactual growth with no drops.

## Running it in XO Space (Quirq)

XO Space reads Claude Code session logs from `~/.claude/projects/` and watches project files. In `claude-code` mode
every agent run is exactly such a session, started with `cwd` at the repo root so it is attributed to this project.

```bash
curl -fsSL https://quirq.ai/install | sh          # local Space, UI at http://localhost:5002/space/
DRAGONFLY_LLM=claude-code DRAGONFLY_XO_REGISTER=1 uvicorn dragonfly.server:app --port 8916
```

Press Play. Under the project (and in the Sessions & Telemetry tab) you get one session per agent run (intake, sentinel, verifier, ... , drone
squad leads) with tokens, cost and the MCP tool calls; in Files you see `incidents/INC-xxxx.md` change on every update.
Write scope for the agents is `incidents/` only (I4): each `claude -p` runs with `--tools ""` and `--restricted`, so
the agent process has no file or shell tools at all; the only writer is `Engine.write_incident`.

## What's real, what's simulated

| real | simulated |
|---|---|
| the 13 agents: Claude Code sessions or Anthropic Messages API, tool-use loops, pydantic-validated JSON, real cost | the fire: a circle that grows and drifts downwind (no spread model) |
| the router, merger, approval gate, incident files | satellite: one hotspot every 10 ticks once the fire is ≥ 100 m |
| `live` tools: NASA FIRMS VIIRS and Open-Meteo (behind `DRAGONFLY_MODE=live`) | citizens: scripted reports, opt-in flags, walk/drive away once contacted |
| | drones: fly at 90 km/h, drop, refill 2 min at base; each drop cancels 2 m/min of growth |
| | phones: the outbox panel |

## Stack

Python 3.12, FastAPI + uvicorn, pydantic v2, anthropic SDK, httpx. Nothing else at runtime. Plain HTML + JS
dashboard with an SVG map. No database, no framework, no websockets.

## Team

_names here_

## License

MIT

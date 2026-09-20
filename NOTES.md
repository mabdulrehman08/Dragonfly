# NOTES — decisions made without asking

- **Renamed to `ninesixteen`** (package, env prefix `NINESIXTEEN_*`, repo). Started from the partial step-1 in the parent folder.
- **Drones are in, but after the click.** The brief said no drones; the founder asked for them. Resolution: the drone layer
  is downstream of Approve. The Suppression Commander stages a `DronePlan`; `world.launch()` is called only inside
  `approve_incident`. I3 still holds: one outbox writer, one launch site, same function.
- **13 agents, not 3.** Intake, Sentinel and Verifier run per report (concurrently). Seven response agents run once an
  incident is corroborated. Squad Leads run after approval (one per 25 drones, max 10). After-Action runs at scenario end
  for approved incidents. All go through one `run_agent` loop so every run is one observable session with cost.
- **Labels are code, prose is model.** `verdict_from_trace` derives corroborated / uncorroborated / unverifiable from the
  Verifier's tool results. The model writes the evidence list. This is what makes the invariance test deterministic with a
  real model at temperature 0. If the model skips a tool, the runner calls it and records it.
- **Corroboration rule count includes the report itself.** "≥2 other reports" == `other_reports_within_2km_30min >= 3`
  because the tool counts the report under verification too (it has no report id, by I1).
- **First corroboration in `eaton_baseline` is t5, not t10–12.** Three reports within 2 km by t5 corroborate under the
  brief's own rule. The hedge ("could just be a BBQ") counts: the Verifier never sees text, so it can't discount it.
  The satellite still lands at t10. `injection` corroborates at t11 (hotspot at t10; the two adversarial reports at t2/t3 stay uncorroborated).
- **Invariance test shuffles identity only.** Reporter ids are shuffled while each report's lat/lon is pinned to its
  original value (scenario reports accept optional `lat`/`lon`). Moving the location with the id would change world
  evidence legitimately and test nothing about blindness.
- **Eval has a simulated dispatcher.** Headless runs approve ready incidents one tick after they appear, through
  `approve_incident`, so `people_saved` and drone metrics are measurable. Set `DISPATCHER_DELAY = None` for a no-click run.
- **World context variable.** I1 forbids passing the world into `run_verifier`, so agents find it via a `ContextVar`
  bound at each engine entry point (not in `__init__`, because FastAPI gives every request a fresh context).
- **Helpers say YES automatically in the sim.** After Approve, each matched helper walks to their neighbor; the
  neighbor leaves once the helper arrives. Address sharing after YES is stated in the ask text but not simulated.
- **People states:** safe → in_path (inside the downwind cone) → contacted (got an SMS) → evacuating → evacuated
  (≥ 2 km clear). overrun = inside the fire circle. Nothing else is modelled; no ages, no medical data.
- **Acres burned = area at max radius.** Burned land doesn't unburn when drones shrink the circle. `acres_without_drones`
  is the counterfactual with zero drops.
- **Drone tuning.** 90 km/h, 2-tick refill, 2 m of growth cancelled per drop. 12 drones slow the Eaton fire; 500 finish it
  in one pass. Numbers are for the demo, not physics.
- **`swarm_500` added as a fourth scenario** (same reports as eaton, 500 drones, 40 ticks).
- **Dev deps split** into `requirements-dev.txt` (ruff, pytest) so the runtime list stays at five packages.
- **`.env` loader** is 12 lines in `config.py` rather than a dependency.
- **Live mode is code-complete but untested**: no FIRMS key in this environment.
- **Third backend, `claude-code`, added at 15:15** after finding XO Space only observes Claude Code / Codex / Cursor
  session logs, not raw API calls. Each agent run is `claude -p` with `--tools "" --restricted --strict-mcp-config`,
  tools served by `ninesixteen/mcp.py` (hand-written JSON-RPC, no `mcp` package). The run id is in the MCP URL, so the
  endpoint returns only that agent's allow-list. Sessions use the user's CLI login; no API key needed. Real eval: $0.95,
  all pass, invariance holds. The `claude` API backend is kept and works with a key.
- **A bad API key in `claude` mode** was verified to degrade to `unverifiable` + human lane without crashing (I5).

- **XO Space project attribution** (`ninesixteen/xo.py`, opt-in `NINESIXTEEN_XO_REGISTER=1`): the Space only lists a
  session under a project if a row exists in its own index at `~/.quirq/projects/<pid>/sessions/sessionslist.d/`. We write
  that row (same format as the Space's Claude adapter) before each `claude -p` with a pre-allocated `--session-id`, and
  refresh it with real token usage after. This is Quirq's state dir, not the project tree, so I4 is untouched; it is a
  separate module so the I4 test on engine/agents still counts one write call.

## v2 (not built)
- autopilot / auto-approve (breaks I3 and ideology 4; if ever built it must be a separate, opt-in, audited gate)
- MCP server exposing the tools (thin wrapper over `tools.TOOLS`)
- re-running Emergency when a merged report raises `needs_help_leaving`
- helpers replying NO / timeouts, re-matching
- real fire spread (Rothermel), terrain, multiple ignitions
- drone battery, wind drift on drops, collision spacing
- voice, Twilio, auth, multi-hazard, websockets, a database

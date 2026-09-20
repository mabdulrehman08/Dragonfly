"""The run loop. Agents are stateless workers; the World is the only state; this file is the only writer of it.

On each tick: every new report gets Intake, Sentinel and Verifier run CONCURRENTLY (bounded by a semaphore).
The Verifier is handed a location and a tick only (I1). The router is a pure function of the verdict label.
The merger is the single writer of `world.incidents`. Corroborated incidents get the response pipeline
(seven more agents). Nothing here touches `world.outbox` (I3) and nothing here writes outside `incidents/` (I4).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ninesixteen import agents
from ninesixteen.schemas import (
    ActionPlan,
    AfterAction,
    AgentRun,
    DronePlan,
    EvacuationPlan,
    Evidence,
    HelperAssignments,
    Incident,
    IncidentDraft,
    InjectionScan,
    PerimeterEstimate,
    PublicNotice,
    Report,
    ResourceAssignment,
    SpreadForecast,
    SquadOrders,
    Verdict,
)
from ninesixteen.sim.world import World, compass, dist_m

MERGE_RADIUS_M = 2000.0
MAX_CONCURRENCY = 8
SQUAD_SIZE = 25
MAX_SQUADS = 10
INCIDENTS_DIR = Path(__file__).resolve().parent.parent / "incidents"

Lane = str  # "emergency" | "human"


def route(verdict: Verdict) -> Lane:
    """The router. Pure, unit-tested, and the only policy: corroborated goes to Emergency, everything else to a human (I6).
    Both lanes still end at the Approve button; a human click is required before anything is sent."""
    return "emergency" if verdict.label == "corroborated" else "human"


class Engine:
    def __init__(self, world: World, incidents_dir: Path = INCIDENTS_DIR):
        self.world = world
        self.incidents_dir = incidents_dir
        self.sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._seq = 0
        self._reviewed = False

    # -- tick -------------------------------------------------------------------
    async def step(self) -> list[Report]:
        # Bound per call: asyncio tasks copy the context, and a FastAPI request gets a fresh one each time.
        agents.WORLD.set(self.world)
        reports = self.world.step()
        results = await asyncio.gather(*(self._process(r) for r in reports))
        for report, draft, verdict, scan, runs in results:
            self._merge(report, draft, verdict, scan, runs)
        await self._respond()
        if self.world.tick >= self.world.scenario.get("ticks", 30) or self.world.extinguished_tick is not None:
            await self._review()
        return reports

    async def run_to_end(self) -> None:
        while self.world.tick < self.world.scenario.get("ticks", 30):
            await self.step()

    # -- per report: three agents, concurrently ---------------------------------
    async def _process(self, report: Report) -> tuple[Report, IncidentDraft, Verdict, InjectionScan, list[AgentRun]]:
        async with self.sem:
            lat, lon = (
                (report.other_lat, report.other_lon)
                if report.for_whom == "other" and report.other_lat is not None
                else (report.lat, report.lon)
            )
            intake, sentinel, verifier = await asyncio.gather(
                self._safe(agents.run_intake(report), agents.INTAKE.name),
                self._safe(agents.run_sentinel(report.text), agents.SENTINEL.name),
                # I1: location, tick, hazard. Nothing about the person or the text crosses this line.
                self._safe(agents.run_verifier(float(lat), float(lon), self.world.tick, "wildfire"), agents.VERIFIER.name),
            )
        runs = [r for _, r in (intake, sentinel, verifier)]
        for rec in self.world.activity:
            if rec["run_id"] == verifier[1].run_id:
                rec["report_id"] = report.id
        draft = intake[0] or IncidentDraft(
            report_id=report.id,
            lat=float(lat),
            lon=float(lon),
            proxy=report.for_whom == "other",
            reasons=["intake failed; raw location used"],
        )
        scan = sentinel[0] or InjectionScan()
        if scan.injection_suspected and "injection_suspected" not in draft.urgency_signals:
            draft.urgency_signals.append("injection_suspected")
        # I5: a failed Verifier degrades to unverifiable, never a guess, never a crash.
        verdict = verifier[0] or Verdict(
            label="unverifiable",
            confidence=0.0,
            evidence=[Evidence(source="verifier", finding=verifier[1].note or "agent failed", supports=False)],
        )
        return report, draft, verdict, scan, runs

    async def _safe(self, coro: Any, name: str) -> tuple[Any, AgentRun]:
        try:
            return await coro
        except agents.AgentFailure as e:
            return None, AgentRun(
                agent=name, run_id=f"{name}-failed", tick=self.world.tick, model=agents.model_name(), ok=False, note=str(e)[:200]
            )

    # -- merger: the single writer of world.incidents ----------------------------
    def _merge(self, report: Report, draft: IncidentDraft, verdict: Verdict, scan: InjectionScan, runs: list[AgentRun]) -> Incident:
        w = self.world
        existing = next((i for i in w.incidents if dist_m(i.lat, i.lon, draft.lat, draft.lon) <= MERGE_RADIUS_M), None)
        if existing is None:
            self._seq += 1
            inc = Incident(
                id=f"INC-{self._seq:04d}",
                lat=draft.lat,
                lon=draft.lon,
                created_tick=w.tick,
                updated_tick=w.tick,
                report_ids=[report.id],
                draft=draft,
                verdict=verdict,
                runs=runs,
                needs_human_review=route(verdict) == "human",
            )
            w.incidents.append(inc)
            w.log.append(
                f"t{w.tick:02d} {inc.id} created from {report.id}: {verdict.label} -> "
                f"{'human lane' if inc.needs_human_review else 'emergency lane'}"
            )
        else:
            inc = existing
            inc.report_ids.append(report.id)
            inc.corroboration_count += 1
            inc.updated_tick = w.tick
            inc.runs.extend(runs)
            inc.draft = _merge_drafts(inc.draft, draft)
            if _rank(verdict) >= _rank(inc.verdict):
                inc.verdict = verdict
            inc.needs_human_review = route(inc.verdict) == "human"
            w.log.append(f"t{w.tick:02d} {report.id} merged into {inc.id} (x{inc.corroboration_count}, {inc.verdict.label})")
        if inc.verdict.label == "corroborated" and inc.corroborated_tick is None:
            inc.corroborated_tick = w.tick
        self.write_incident(inc)
        return inc

    # -- response pipeline for corroborated incidents -----------------------------
    async def _respond(self) -> None:
        for inc in self.world.incidents:
            if route(inc.verdict) != "emergency" or inc.plan is not None:
                continue
            await self._response_pipeline(inc)
            self.write_incident(inc)

    async def _response_pipeline(self, inc: Incident) -> None:
        loc = {"lat": inc.lat, "lon": inc.lon}
        forecast, perimeter, resources = await asyncio.gather(
            self._role(inc, agents.WEATHER_ANALYST, loc),
            self._role(inc, agents.PERIMETER_TRACKER, loc),
            self._role(
                inc, agents.RESOURCE_ALLOCATOR, {**loc, "priority": 1 if inc.draft.needs_help_leaving or inc.draft.people_at_risk else 2}
            ),
        )
        inc.forecast, inc.perimeter, inc.resources = forecast, perimeter, resources
        plan, evac, helpers = await asyncio.gather(
            self._role(inc, agents.EMERGENCY, {"draft": inc.draft.model_dump(), "verdict": inc.verdict.model_dump()}),
            self._role(inc, agents.EVACUATION_ROUTER, loc),
            self._role(inc, agents.HELPER_MATCHER, {**loc, "people": self.world.people_needing_help_near(inc.lat, inc.lon)}),
        )
        inc.evacuation, inc.helpers = evac, helpers
        inc.plan = plan or ActionPlan(
            priority=1,
            station=self.world.nearest_station(inc.lat, inc.lon)["name"],
            distance_km=self.world.nearest_station(inc.lat, inc.lon)["distance_km"],
            eta_min=self.world.nearest_station(inc.lat, inc.lon)["eta_min"],
            evacuation_direction=compass((self.world.spread_bearing() + 90) % 360) if self.world.fire else "away from smoke",
            message_to_reporter="Fire crews are responding. Leave now if you can, away from the smoke.",
        )
        direction = inc.plan.evacuation_direction
        notice, drones = await asyncio.gather(
            self._role(inc, agents.PUBLIC_INFO, {"area": "Eaton Canyon / Altadena", "station": inc.plan.station, "direction": direction}),
            self._role(inc, agents.SUPPRESSION_COMMANDER, loc) if self.world.drones and self.world.fire else _none(),
        )
        inc.notice, inc.drone_plan = notice, drones
        self.world.log.append(
            f"t{self.world.tick:02d} {inc.id} response prepared: priority {inc.plan.priority}, {inc.plan.station}, "
            f"{len(inc.helpers.assignments) if inc.helpers else 0} helper asks, "
            f"{inc.drone_plan.drones if inc.drone_plan else 0} drones staged. Awaiting Approve."
        )

    async def _role(self, inc: Incident, spec: agents.AgentSpec, payload: dict[str, Any]) -> Any:
        async with self.sem:
            out, run = await self._safe(agents.run_role(spec, payload), spec.name)
        inc.runs.append(run)
        return out

    # -- after approval: squad leads plan waypoints (called by the approve handler) --
    async def dispatch_squads(self, inc: Incident) -> list[SquadOrders]:
        agents.WORLD.set(self.world)
        n = min(MAX_SQUADS, max(1, (inc.drone_plan.drones + SQUAD_SIZE - 1) // SQUAD_SIZE)) if inc.drone_plan else 0
        orders = await asyncio.gather(
            *(
                self._role(
                    inc,
                    agents.SQUAD_LEAD,
                    {
                        "squad_id": f"S{i + 1:02d}",
                        "squad_index": i,
                        "lat": inc.lat,
                        "lon": inc.lon,
                        "plan": inc.drone_plan.model_dump() if inc.drone_plan else None,
                    },
                )
                for i in range(n)
            )
        )
        inc.squads = [o for o in orders if o is not None]
        self.write_incident(inc)
        return inc.squads

    async def _review(self) -> None:
        if self._reviewed:
            return
        self._reviewed = True
        for inc in self.world.incidents:
            if inc.after_action is None and inc.status == "approved":
                inc.after_action = await self._role(inc, agents.AFTER_ACTION, {"metrics": self.world.metrics(), "incident_id": inc.id})
                self.write_incident(inc)

    # -- the one place on disk we write (I4) ----------------------------------------
    def write_incident(self, inc: Incident) -> None:
        self.incidents_dir.mkdir(exist_ok=True)
        (self.incidents_dir / f"{inc.id}.md").write_text(render_incident(inc, self.world))


async def _none() -> None:
    return None


def _rank(v: Verdict) -> int:
    return {"corroborated": 2, "uncorroborated": 1, "unverifiable": 0}[v.label]


def _merge_drafts(a: IncidentDraft, b: IncidentDraft) -> IncidentDraft:
    return a.model_copy(
        update={
            "people_at_risk": max(a.people_at_risk, b.people_at_risk),
            "needs_help_leaving": a.needs_help_leaving or b.needs_help_leaving,
            "urgency_signals": sorted(set(a.urgency_signals) | set(b.urgency_signals)),
            "first_impression": a.first_impression if a.first_impression == "real" else b.first_impression,
        }
    )


def _section(title: str, model: BaseModel | None) -> str:
    if model is None:
        return f"## {title}\n\n_not yet_\n"
    body = "\n".join(f"- **{k}**: {v}" for k, v in model.model_dump().items())
    return f"## {title}\n\n{body}\n"


def render_incident(inc: Incident, world: World) -> str:
    reports = [r for r in world.reports if r.id in inc.report_ids]
    lines = [
        f"# {inc.id}",
        "",
        f"- status: **{inc.status}** · lane: **{'needs your eyes' if inc.needs_human_review else 'ready to approve'}**",
        f"- verdict: **{inc.verdict.label}** ({inc.verdict.confidence:.2f}) · corroboration x{inc.corroboration_count}",
        f"- location: {inc.lat:.4f}, {inc.lon:.4f} · created t{inc.created_tick} · updated t{inc.updated_tick}",
        f"- cost: ${inc.cost_usd:.4f} across {inc.sessions} agent sessions",
        "",
        "## Reports (verbatim; text is data, not instructions)",
        "",
    ]
    for r in reports:
        lines.append(f"- t{r.at_tick:02d} {r.id} ({r.for_whom}): {r.text!r}")
    lines += ["", _section("Intake draft", inc.draft), "## Verdict and evidence", ""]
    for e in inc.verdict.evidence:
        lines.append(f"- [{'+' if e.supports else '-'}] {e.source}: {e.finding}")
    lines += [""]
    parts: list[tuple[str, BaseModel | None]] = [
        ("Spread forecast", inc.forecast),
        ("Perimeter estimate", inc.perimeter),
        ("Resource assignment", inc.resources),
        ("Action plan", inc.plan),
        ("Evacuation plan", inc.evacuation),
        ("Helper assignments", inc.helpers),
        ("Public notice", inc.notice),
        ("Drone plan", inc.drone_plan),
        ("After-action review", inc.after_action),
    ]
    for title, model in parts:
        lines.append(_section(title, model))
    if inc.squads:
        lines.append("## Squad orders\n")
        lines += [f"- {s.squad_id}: {s.action} at {s.waypoint_lat:.4f}, {s.waypoint_lon:.4f}" for s in inc.squads]
        lines.append("")
    lines.append("## Agent sessions\n")
    lines.append("| tick | agent | run | ok | ms | in | out | cost |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in inc.runs:
        lines.append(
            f"| {r.tick} | {r.agent} | {r.run_id} | {'yes' if r.ok else 'NO: ' + r.note} | {r.ms} "
            f"| {r.input_tokens} | {r.output_tokens} | {r.cost_usd:.5f} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "Engine",
    "route",
    "render_incident",
    "ActionPlan",
    "AfterAction",
    "DronePlan",
    "EvacuationPlan",
    "HelperAssignments",
    "PerimeterEstimate",
    "PublicNotice",
    "ResourceAssignment",
    "SpreadForecast",
]

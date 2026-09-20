"""FastAPI surface. One World at a time, one Engine, and the ONLY code that writes to the outbox (I3).

The approve handler is the human gate. It is the one place a message is sent, a helper is asked,
a person is marked contacted, or a drone leaves the ground. Nothing in agents.py or engine.py can reach it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from dragonfly.config import load_dotenv
from dragonfly.engine import INCIDENTS_DIR, Engine
from dragonfly.mcp import mcp_app
from dragonfly.schemas import Incident
from dragonfly.sim.world import World, compass, list_scenarios, load_scenario

STATIC = Path(__file__).resolve().parent / "static"
PLAY_INTERVAL_S = 1.5

load_dotenv()
app = FastAPI(title="Dragonfly")
app.mount("/mcp", mcp_app)


class Session:
    """The one mutable thing the server owns: the current world, its engine, and the play task."""

    def __init__(self, scenario: str = "eaton_baseline"):
        self.reset(scenario)

    def reset(self, scenario: str) -> None:
        for stale in INCIDENTS_DIR.glob("INC-*.md"):  # a fresh world gets a fresh incidents/ folder (still inside I4's scope)
            stale.unlink()
        self.world = World(load_scenario(scenario))
        self.engine = Engine(self.world)
        self.playing = False
        self.lock = asyncio.Lock()


session = Session()


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return (STATIC / "dashboard.html").read_text()


@app.get("/state")
async def state() -> JSONResponse:
    snap = session.world.snapshot()
    snap["playing"] = session.playing
    snap["scenarios"] = list_scenarios()
    return JSONResponse(snap)


@app.get("/scenarios")
async def scenarios() -> list[str]:
    return list_scenarios()


@app.post("/reset/{scenario}")
async def reset(scenario: str) -> dict:
    if scenario not in list_scenarios():
        raise HTTPException(404, "unknown scenario")
    session.playing = False
    async with session.lock:
        session.reset(scenario)
    return {"ok": True, "scenario": scenario}


@app.post("/step")
async def step() -> dict:
    async with session.lock:
        reports = await session.engine.step()
    return {"tick": session.world.tick, "reports": [r.id for r in reports]}


@app.post("/play")
async def play() -> dict:
    if session.playing:
        session.playing = False
        return {"playing": False}
    session.playing = True
    asyncio.create_task(_play_loop())
    return {"playing": True}


async def _play_loop() -> None:
    world = session.world
    while session.playing and session.world is world and world.tick < world.scenario.get("ticks", 30):
        async with session.lock:
            await session.engine.step()
        await asyncio.sleep(PLAY_INTERVAL_S)
    if session.world is world:
        session.playing = False


@app.post("/approve/{incident_id}")
async def approve(incident_id: str) -> dict:
    """The human gate. Everything that leaves the system leaves through here."""
    async with session.lock:
        world, engine = session.world, session.engine
        inc = next((i for i in world.incidents if i.id == incident_id), None)
        if inc is None:
            raise HTTPException(404, "unknown incident")
        result = approve_incident(world, engine, inc)
    if result.get("drones_launched"):
        result["squads"] = len(await engine.dispatch_squads(inc))
    return result


def approve_incident(world: World, engine: Engine, inc: Incident) -> dict:
    """The only outbox writer in the codebase (I3); a grep for the append call finds this function alone.
    Called by the /approve route, and by eval.py's simulated dispatcher so headless runs exercise the same gate."""
    if inc.status == "approved":
        return {"ok": True, "already": True, "sent": 0, "drones_launched": 0}
    inc.status = "approved"
    inc.approved_tick = world.tick
    inc.needs_human_review = False
    sent: list[dict] = []

    def send(to: str, kind: str, text: str, helper_id: str | None = None) -> None:
        if any(m["to"] == to and m["kind"] == kind for m in sent):
            return
        msg = {"tick": world.tick, "incident": inc.id, "to": to, "kind": kind, "text": text}
        world.outbox.append(msg)
        sent.append(msg)
        world.mark_contacted(to, helper_id)

    reporters = {world.citizen(r.reporter_id)["id"] for r in world.reports if r.id in inc.report_ids}
    plan = inc.plan
    if plan:
        for rid in sorted(reporters):
            send(rid, "reporter", plan.message_to_reporter)
        for hid in plan.helper_ids:
            send(hid, "helper", plan.message_to_helpers)
    else:
        for rid in sorted(reporters):
            send(rid, "reporter", "A dispatcher has seen your report and is reviewing it now. If you see flames, leave and call 911.")
    if inc.helpers:
        for a in inc.helpers.assignments:
            send(a.helper_id, "helper", a.ask_text)
            # the neighbor said YES in the simulation; the helper starts walking
            world.mark_contacted(a.person_id, a.helper_id)
    if inc.notice:
        # the plan's orders plus whoever has walked into the path since the plan was written
        directions = {o.person_id: o.direction for o in inc.evacuation.orders} if inc.evacuation else {}
        for person in world.people_in_path():
            directions.setdefault(person["id"], compass(person["bearing_from_fire"]))
        for pid, direction in directions.items():
            if pid not in reporters:
                send(pid, "alert", f"{inc.notice.text} Your direction: {direction}.")
    launched = 0
    if inc.drone_plan and inc.drone_plan.drones > 0 and world.fire:
        launched = world.launch(inc.drone_plan)
    world.log.append(f"t{world.tick:02d} APPROVED {inc.id} by dispatcher: {len(sent)} messages, {launched} drones")
    engine.write_incident(inc)
    return {"ok": True, "sent": len(sent), "drones_launched": launched}

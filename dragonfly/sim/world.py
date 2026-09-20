"""The virtual world: one state object and a clock that advances it one simulated minute at a time.

There is no fire model here on purpose. A fire is a circle that grows and drifts downwind.
Citizens do not think; the scenario file says who reports what and when. Once a human approves
an incident, citizens who were told to leave walk or drive away from the fire, and drones fly.

Everything an agent can learn about the world comes through `dragonfly.tools`, which read this object.
The World is the only mutable state in the system.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from dragonfly.schemas import DronePlan, Incident, Report

DATA = Path(__file__).resolve().parent.parent / "data"
SCENARIOS = Path(__file__).resolve().parent / "scenarios"

EARTH_M = 6_371_000.0
SQM_PER_ACRE = 4046.86

DRONE_SPEED_M_PER_TICK = 1500.0  # ~90 km/h
DRONE_REFILL_TICKS = 2
DROP_RADIUS_M = 200.0
PERSON_SPEED_M_PER_TICK = {"drive": 600.0, "walk": 80.0}
SAFE_MARGIN_M = 2000.0


def dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2 (0 = north, 90 = east)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def offset(lat: float, lon: float, meters: float, bearing: float) -> tuple[float, float]:
    """Move a point `meters` along `bearing` (0 = north, 90 = east)."""
    b = math.radians(bearing)
    dlat = meters * math.cos(b) / EARTH_M
    dlon = meters * math.sin(b) / (EARTH_M * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def compass(bearing: float) -> str:
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((bearing + 22.5) // 45) % 8]


def angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def load_scenario(name: str) -> dict:
    return json.loads((SCENARIOS / f"{name}.json").read_text())


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIOS.glob("*.json"))


class World:
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.tick = 0
        self.fire: dict | None = dict(scenario["fire"]) if scenario.get("fire") else None
        self.weather: dict = scenario.get("weather") or {k: scenario["fire"][k] for k in ("wind_deg", "wind_kmh", "rh", "temp_c")}
        self.citizens: list[dict] = json.loads((DATA / "people.json").read_text())
        self.stations: list[dict] = json.loads((DATA / "stations.json").read_text())
        for c in self.citizens:
            c.update(state="safe", home_lat=c["lat"], home_lon=c["lon"], mode="drive", helper_id=None)
        self.reports: list[Report] = []
        self.hotspots: list[dict] = []
        self.incidents: list[Incident] = []
        self.outbox: list[dict] = []
        self.log: list[str] = []
        self.activity: list[dict] = []
        self._by_id = {c["id"]: c for c in self.citizens}
        self._report_seq = 0
        # -- fire bookkeeping --
        self.counterfactual_radius_m = self.fire["radius_m"] if self.fire else 0.0
        self.max_radius_m = self.counterfactual_radius_m
        self.contained_tick: int | None = None
        self.extinguished_tick: int | None = None
        self.drops = 0
        # -- drones: parked at base until a human approves a DronePlan --
        cfg = scenario.get("drones") or {"count": 0, "suppression_m_per_drop": 3.0}
        base = self.stations[cfg.get("base_station", 1)]
        self.drone_base = {"lat": base["lat"], "lon": base["lon"], "name": base["name"]}
        self.suppression_m_per_drop = float(cfg.get("suppression_m_per_drop", 3.0))
        self.drones: list[dict] = [
            {"id": f"D{i + 1:03d}", "lat": base["lat"], "lon": base["lon"], "state": "base", "refill_left": 0}
            for i in range(int(cfg.get("count", 0)))
        ]
        self.mission: DronePlan | None = None

    # -- clock -------------------------------------------------------------
    def step(self) -> list[Report]:
        """Advance one minute. Returns the reports that fired this tick."""
        self.tick += 1
        self._fly_drones()
        self._grow_fire()
        self._update_satellite()
        self._move_people()
        return self._fire_scripted_reports()

    def spread_bearing(self) -> float:
        """wind_deg is where wind blows FROM; the fire head moves the opposite way."""
        return (self.weather["wind_deg"] + 180) % 360

    def _grow_fire(self) -> None:
        if not self.fire:
            return
        f = self.fire
        drops_now = sum(1 for d in self.drones if d["state"] == "returning" and d.get("dropped_tick") == self.tick)
        raw = f["growth_m_per_tick"]
        self.counterfactual_radius_m += raw
        if self.extinguished_tick is not None:
            return
        effective = raw - drops_now * self.suppression_m_per_drop
        if effective <= 0 and self.contained_tick is None:
            self.contained_tick = self.tick
            self.log.append(f"t{self.tick:02d} fire CONTAINED: {drops_now} drops this minute outpace growth")
        f["radius_m"] = max(0.0, f["radius_m"] + effective)
        self.max_radius_m = max(self.max_radius_m, f["radius_m"])
        if effective > 0:
            f["lat"], f["lon"] = offset(f["lat"], f["lon"], effective * 0.4, self.spread_bearing())
        if f["radius_m"] <= 10.0 and self.contained_tick is not None:
            self.extinguished_tick = self.tick
            f["radius_m"] = 0.0
            self.log.append(f"t{self.tick:02d} fire EXTINGUISHED after {self.drops} drops")

    def _update_satellite(self) -> None:
        """Satellites don't see a fire instantly. Drop a detection every N ticks once it's big enough to see."""
        every = self.scenario.get("satellite_every_ticks", 10)
        if self.fire and self.tick % every == 0 and self.fire["radius_m"] >= 100:
            self.hotspots.append(
                {"lat": self.fire["lat"], "lon": self.fire["lon"], "tick": self.tick, "confidence": "high", "source": "SIM-VIIRS"}
            )
            self.log.append(f"t{self.tick:02d} satellite: hotspot detected")

    def _fire_scripted_reports(self) -> list[Report]:
        fired = []
        for r in self.scenario["reports"]:
            if r["at_tick"] != self.tick:
                continue
            who = self._by_id[r["from"]]
            other = self._by_id.get(r.get("other", "")) if r.get("for") == "other" else None
            self._report_seq += 1
            rep = Report(
                id=f"R{self._report_seq:03d}",
                at_tick=self.tick,
                reporter_id=who["id"],
                lat=r.get("lat", who["lat"]),  # a scenario may pin a report's location (the invariance test does)
                lon=r.get("lon", who["lon"]),
                for_whom=r.get("for", "self"),
                other_lat=other["lat"] if other else None,
                other_lon=other["lon"] if other else None,
                text=r["text"],
            )
            self.reports.append(rep)
            fired.append(rep)
            self.log.append(f"t{self.tick:02d} report {rep.id} from {who['name']}")
        return fired

    # -- people: at risk, contacted, moving ---------------------------------
    def in_danger(self, lat: float, lon: float) -> bool:
        """Inside the fire, near its edge, or in the downwind cone it is projected to reach within 30 min."""
        if not self.fire or self.extinguished_tick is not None:
            return False
        f = self.fire
        d = dist_m(f["lat"], f["lon"], lat, lon)
        if d <= f["radius_m"] + 400:
            return True
        cone = angle_diff(bearing_deg(f["lat"], f["lon"], lat, lon), self.spread_bearing()) <= 50
        return cone and d <= f["radius_m"] + self.envelope_m()

    def envelope_m(self) -> float:
        """How far the head is expected to travel in 30 min, capped so it stays a neighborhood."""
        if not self.fire:
            return 0.0
        return min(2500.0, 30 * self.fire["growth_m_per_tick"] * 1.4)

    def _move_people(self) -> None:
        if not self.fire:
            return
        f = self.fire
        for c in self.citizens:
            st = c["state"]
            if st in ("evacuated", "overrun"):
                continue
            inside = dist_m(f["lat"], f["lon"], c["lat"], c["lon"]) <= f["radius_m"]
            if inside:
                c["state"] = "overrun"
                self.log.append(f"t{self.tick:02d} {c['name']} was overrun by the fire")
                continue
            if st == "safe" and self.in_danger(c["lat"], c["lon"]):
                c["state"] = "in_path"
            elif st == "contacted":
                waiting = c["opt_in"] == "may_need_help" and not self._helper_arrived(c)
                if not waiting:
                    c["state"] = "evacuating"
            elif st == "evacuating":
                away = bearing_deg(f["lat"], f["lon"], c["lat"], c["lon"])
                speed = PERSON_SPEED_M_PER_TICK["walk" if c["opt_in"] == "may_need_help" else c["mode"]]
                c["lat"], c["lon"] = offset(c["lat"], c["lon"], speed, away)
                if (
                    not self.in_danger(c["lat"], c["lon"])
                    and dist_m(f["lat"], f["lon"], c["lat"], c["lon"]) >= f["radius_m"] + SAFE_MARGIN_M
                ):
                    c["state"] = "evacuated"
                    self.log.append(f"t{self.tick:02d} {c['name']} reached safety")

    def _helper_arrived(self, person: dict) -> bool:
        """A helper who said YES walks to the person; they leave together once the helper is there."""
        hid = person.get("helper_id")
        if not hid:
            return False
        h = self._by_id[hid]
        d = dist_m(h["lat"], h["lon"], person["lat"], person["lon"])
        if d <= 60:
            return True
        h["lat"], h["lon"] = offset(h["lat"], h["lon"], min(d, 200.0), bearing_deg(h["lat"], h["lon"], person["lat"], person["lon"]))
        return False

    def mark_contacted(self, person_id: str, helper_id: str | None = None) -> None:
        """Called by the approve handler for every recipient of an outbox message."""
        c = self._by_id.get(person_id)
        if not c:
            return
        if helper_id:
            c["helper_id"] = helper_id
        if c["state"] in ("safe", "in_path"):
            c["state"] = "contacted"

    # -- drones ---------------------------------------------------------------
    def launch(self, plan: DronePlan) -> int:
        """Arm the fleet. Only the approve handler calls this (I3). Returns the number of drones launched."""
        self.mission = plan
        n = 0
        for d in self.drones:
            if n >= plan.drones:
                break
            if d["state"] == "base":
                d["state"] = "enroute"
                n += 1
        self.log.append(f"t{self.tick:02d} {n} drones launched ({plan.pattern})")
        return n

    def drop_target(self, drone_idx: int) -> tuple[float, float]:
        """Where to drop: the downwind head by default; flanks or a ring spread the fleet around the edge."""
        f = self.fire
        assert f is not None and self.mission is not None
        b = self.mission.target_bearing_deg
        if self.mission.pattern == "flank_attack":
            b = (b + (90 if drone_idx % 2 else -90)) % 360
        elif self.mission.pattern == "perimeter_ring":
            b = (b + (drone_idx * 37) % 360) % 360
        return offset(f["lat"], f["lon"], max(f["radius_m"], 30.0), b)

    def _fly_drones(self) -> None:
        if not self.mission or not self.fire:
            return
        fire_out = self.extinguished_tick is not None
        for i, d in enumerate(self.drones):
            st = d["state"]
            if st == "base":
                continue
            if st == "enroute":
                if fire_out:
                    d["state"] = "returning"
                    continue
                tl, tn = self.drop_target(i)
                dist = dist_m(d["lat"], d["lon"], tl, tn)
                if dist <= DROP_RADIUS_M:
                    d["state"], d["dropped_tick"] = "returning", self.tick
                    self.drops += 1
                else:
                    d["lat"], d["lon"] = offset(
                        d["lat"], d["lon"], min(dist, DRONE_SPEED_M_PER_TICK), bearing_deg(d["lat"], d["lon"], tl, tn)
                    )
            elif st == "returning":
                bl, bn = self.drone_base["lat"], self.drone_base["lon"]
                dist = dist_m(d["lat"], d["lon"], bl, bn)
                if dist <= DROP_RADIUS_M:
                    d["lat"], d["lon"] = bl, bn
                    d["state"], d["refill_left"] = ("base", 0) if fire_out else ("refill", DRONE_REFILL_TICKS)
                else:
                    d["lat"], d["lon"] = offset(
                        d["lat"], d["lon"], min(dist, DRONE_SPEED_M_PER_TICK), bearing_deg(d["lat"], d["lon"], bl, bn)
                    )
            elif st == "refill":
                d["refill_left"] -= 1
                if d["refill_left"] <= 0:
                    d["state"] = "base" if fire_out else "enroute"

    # -- queries used by tools ---------------------------------------------
    def hotspots_near(self, lat: float, lon: float, radius_m: float = 5000) -> list[dict]:
        out = []
        for h in self.hotspots:
            d = dist_m(lat, lon, h["lat"], h["lon"])
            if d <= radius_m:
                out.append({**h, "distance_km": round(d / 1000, 1), "minutes_ago": self.tick - h["tick"]})
        return sorted(out, key=lambda h: h["distance_km"])

    def reports_near(self, lat: float, lon: float, radius_m: float = 2000, minutes: int = 30, exclude_id: str | None = None) -> int:
        return sum(
            1
            for r in self.reports
            if r.id != exclude_id and self.tick - r.at_tick <= minutes and dist_m(lat, lon, r.lat, r.lon) <= radius_m
        )

    def nearest_station(self, lat: float, lon: float) -> dict:
        best = min(self.stations, key=lambda s: dist_m(lat, lon, s["lat"], s["lon"]))
        d_km = dist_m(lat, lon, best["lat"], best["lon"]) / 1000
        return {"name": best["name"], "distance_km": round(d_km, 1), "eta_min": max(2, round(d_km / 40 * 60) + 2)}

    def stations_ranked(self, lat: float, lon: float) -> list[dict]:
        out = []
        for s in self.stations:
            d_km = dist_m(lat, lon, s["lat"], s["lon"]) / 1000
            out.append({"name": s["name"], "distance_km": round(d_km, 1), "eta_min": max(2, round(d_km / 40 * 60) + 2)})
        return sorted(out, key=lambda s: s["distance_km"])

    def helpers_near(self, lat: float, lon: float, radius_m: float = 500) -> list[dict]:
        return [
            {"id": c["id"], "name": c["name"], "distance_m": round(dist_m(lat, lon, c["lat"], c["lon"]))}
            for c in self.citizens
            if c["opt_in"] == "can_help" and c["state"] not in ("overrun",) and dist_m(lat, lon, c["lat"], c["lon"]) <= radius_m
        ]

    def people_in_path(self) -> list[dict]:
        return [
            {
                "id": c["id"],
                "name": c["name"],
                "lat": c["lat"],
                "lon": c["lon"],
                "opt_in": c["opt_in"],
                "state": c["state"],
                "bearing_from_fire": round(bearing_deg(self.fire["lat"], self.fire["lon"], c["lat"], c["lon"])),
            }
            for c in self.citizens
            if c["state"] in ("in_path", "contacted") and self.fire
        ]

    def people_needing_help_near(self, lat: float, lon: float, radius_m: float = 3000) -> list[dict]:
        return [
            {"id": c["id"], "name": c["name"], "distance_m": round(dist_m(lat, lon, c["lat"], c["lon"])), "state": c["state"]}
            for c in self.citizens
            if c["opt_in"] == "may_need_help"
            and c["state"] not in ("evacuated", "overrun")
            and dist_m(lat, lon, c["lat"], c["lon"]) <= radius_m
        ]

    def fleet_status(self) -> dict:
        counts: dict[str, int] = {}
        for d in self.drones:
            counts[d["state"]] = counts.get(d["state"], 0) + 1
        base_km = dist_m(self.drone_base["lat"], self.drone_base["lon"], self.fire["lat"], self.fire["lon"]) / 1000 if self.fire else None
        return {
            "total": len(self.drones),
            "by_state": counts,
            "base": self.drone_base["name"],
            "base_to_fire_km": round(base_km, 1) if base_km is not None else None,
            "suppression_m_per_drop": self.suppression_m_per_drop,
            "drops_so_far": self.drops,
        }

    def citizen(self, cid: str) -> dict:
        return self._by_id[cid]

    # -- serialization for the dashboard -----------------------------------
    def snapshot(self) -> dict:
        reported = {r.reporter_id for r in self.reports}
        return {
            "scenario": self.scenario["name"],
            "tick": self.tick,
            "ticks": self.scenario.get("ticks", 30),
            "fire": self.fire,
            "weather": self.weather,
            "spread_bearing": self.spread_bearing() if self.fire else None,
            "envelope_m": self.envelope_m(),
            "citizens": [{**c, "reported": c["id"] in reported} for c in self.citizens],
            "stations": self.stations,
            "hotspots": self.hotspots,
            "drones": self.drones[:600],
            "drone_base": self.drone_base,
            "mission": self.mission.model_dump() if self.mission else None,
            "reports": [r.model_dump() for r in self.reports],
            "incidents": [self.incident_dump(i) for i in self.incidents],
            "outbox": self.outbox,
            "log": self.log[-60:],
            "activity": self.activity[-80:],
            "metrics": self.metrics(),
        }

    @staticmethod
    def incident_dump(i: Incident) -> dict:
        d = i.model_dump()
        d["cost_usd"], d["sessions"] = i.cost_usd, i.sessions
        return d

    def metrics(self) -> dict:
        inc = self.incidents
        first = min((i.corroborated_tick for i in inc if i.corroborated_tick is not None), default=None)
        states = [c["state"] for c in self.citizens]
        acres = math.pi * self.max_radius_m**2 / SQM_PER_ACRE if self.fire else 0.0
        cf = math.pi * self.counterfactual_radius_m**2 / SQM_PER_ACRE if self.fire else 0.0
        return {
            "reports": len(self.reports),
            "incidents": len(inc),
            "corroborated": sum(1 for i in inc if i.verdict.label == "corroborated"),
            "human_review": sum(1 for i in inc if i.needs_human_review),
            "approved": sum(1 for i in inc if i.status == "approved"),
            "dismissed": 0,  # no code path can dismiss; kept explicit so the dashboard can show it
            "first_corroborated_tick": first,
            "people_at_risk": sum(1 for s in states if s in ("in_path", "contacted", "evacuating")) if self.fire else 0,
            "people_saved": states.count("evacuated"),
            "people_overrun": states.count("overrun"),
            "drones_active": sum(1 for d in self.drones if d["state"] != "base"),
            "drops": self.drops,
            "contained_tick": self.contained_tick,
            "extinguished_tick": self.extinguished_tick,
            "acres_burned": round(acres, 1),
            "acres_without_drones": round(cf, 1),
            "cost_usd": round(sum(i.cost_usd for i in inc), 4),
            "sessions": sum(i.sessions for i in inc),
            "failed_runs": sum(1 for a in self.activity if not a["ok"]),
        }

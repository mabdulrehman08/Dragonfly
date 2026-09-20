"""The virtual world: one state object and a clock that advances it one simulated minute at a time.

There is no fire model here on purpose. A fire is a circle that grows and drifts downwind.
Citizens do not think; the scenario file says who reports what and when.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ninesixteen.schemas import Report

DATA = Path(__file__).resolve().parent.parent / "data"
SCENARIOS = Path(__file__).resolve().parent / "scenarios"

EARTH_M = 6_371_000.0


def dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(a))


def offset(lat: float, lon: float, meters: float, bearing_deg: float) -> tuple[float, float]:
    """Move a point `meters` along `bearing_deg` (0 = north, 90 = east)."""
    b = math.radians(bearing_deg)
    dlat = meters * math.cos(b) / EARTH_M
    dlon = meters * math.sin(b) / (EARTH_M * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def load_scenario(name: str) -> dict:
    return json.loads((SCENARIOS / f"{name}.json").read_text())


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIOS.glob("*.json"))


class World:
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.tick = 0
        self.fire = dict(scenario["fire"]) if scenario.get("fire") else None
        self.weather = scenario.get("weather") or {
            k: scenario["fire"][k] for k in ("wind_deg", "wind_kmh", "rh", "temp_c")
        }
        self.citizens: list[dict] = json.loads((DATA / "people.json").read_text())
        self.stations: list[dict] = json.loads((DATA / "stations.json").read_text())
        self.reports: list[Report] = []
        self.hotspots: list[dict] = []
        self.incidents: list = []
        self.outbox: list[dict] = []
        self.log: list[str] = []
        self._by_id = {c["id"]: c for c in self.citizens}
        self._report_seq = 0

    # -- clock -------------------------------------------------------------
    def step(self) -> list[Report]:
        """Advance one minute. Returns the reports that fired this tick."""
        self.tick += 1
        self._grow_fire()
        self._update_satellite()
        return self._fire_scripted_reports()

    def _grow_fire(self) -> None:
        if not self.fire:
            return
        f = self.fire
        f["radius_m"] += f["growth_m_per_tick"]
        # wind_deg is where wind blows FROM; the fire head moves the opposite way
        f["lat"], f["lon"] = offset(f["lat"], f["lon"], f["growth_m_per_tick"] * 0.4, (f["wind_deg"] + 180) % 360)

    def _update_satellite(self) -> None:
        """Satellites don't see a fire instantly. Drop a detection every N ticks once it's big enough to see."""
        every = self.scenario.get("satellite_every_ticks", 10)
        if self.fire and self.tick % every == 0 and self.fire["radius_m"] >= 100:
            self.hotspots.append({"lat": self.fire["lat"], "lon": self.fire["lon"], "tick": self.tick,
                                  "confidence": "high", "source": "SIM-VIIRS"})
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
                id=f"R{self._report_seq:03d}", at_tick=self.tick, reporter_id=who["id"],
                lat=who["lat"], lon=who["lon"], for_whom=r.get("for", "self"),
                other_lat=other["lat"] if other else None, other_lon=other["lon"] if other else None,
                text=r["text"],
            )
            self.reports.append(rep)
            fired.append(rep)
            self.log.append(f"t{self.tick:02d} report {rep.id} from {who['name']}")
        return fired

    # -- queries used by tools ---------------------------------------------
    def hotspots_near(self, lat: float, lon: float, radius_m: float = 5000) -> list[dict]:
        out = []
        for h in self.hotspots:
            d = dist_m(lat, lon, h["lat"], h["lon"])
            if d <= radius_m:
                out.append({**h, "distance_km": round(d / 1000, 1), "minutes_ago": self.tick - h["tick"]})
        return out

    def reports_near(self, lat: float, lon: float, radius_m: float = 2000, minutes: int = 30,
                     exclude_id: str | None = None) -> int:
        return sum(
            1 for r in self.reports
            if r.id != exclude_id and self.tick - r.at_tick <= minutes
            and dist_m(lat, lon, r.lat, r.lon) <= radius_m
        )

    def nearest_station(self, lat: float, lon: float) -> dict:
        best = min(self.stations, key=lambda s: dist_m(lat, lon, s["lat"], s["lon"]))
        d_km = dist_m(lat, lon, best["lat"], best["lon"]) / 1000
        return {"name": best["name"], "distance_km": round(d_km, 1), "eta_min": max(2, round(d_km / 40 * 60) + 2)}

    def helpers_near(self, lat: float, lon: float, radius_m: float = 500) -> list[dict]:
        return [
            {"id": c["id"], "name": c["name"], "distance_m": round(dist_m(lat, lon, c["lat"], c["lon"]))}
            for c in self.citizens
            if c["opt_in"] == "can_help" and dist_m(lat, lon, c["lat"], c["lon"]) <= radius_m
        ]

    def citizen(self, cid: str) -> dict:
        return self._by_id[cid]

    # -- serialization for the dashboard -----------------------------------
    def snapshot(self) -> dict:
        reported = {r.reporter_id for r in self.reports}
        return {
            "scenario": self.scenario["name"], "tick": self.tick, "fire": self.fire, "weather": self.weather,
            "citizens": [{**c, "reported": c["id"] in reported} for c in self.citizens],
            "stations": self.stations, "hotspots": self.hotspots,
            "reports": [r.model_dump() for r in self.reports],
            "incidents": [i.model_dump() for i in self.incidents],
            "outbox": self.outbox, "log": self.log[-40:],
            "metrics": self.metrics(),
        }

    def metrics(self) -> dict:
        inc = self.incidents
        first = next((i.updated_tick for i in inc if i.verdict.label == "corroborated"), None)
        return {
            "reports": len(self.reports),
            "incidents": len(inc),
            "corroborated": sum(1 for i in inc if i.verdict.label == "corroborated"),
            "human_review": sum(1 for i in inc if i.needs_human_review),
            "dismissed": 0,  # no code path can dismiss; kept explicit so the dashboard can show it
            "first_corroborated_tick": first,
            "cost_usd": round(sum(i.cost_usd for i in inc), 4),
            "sessions": sum(i.sessions for i in inc),
        }

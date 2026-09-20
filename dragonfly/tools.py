"""Tools the agents may call. Each has a `sim` and a `live` behavior behind one flag.

The agents never know which one they're talking to. Every tool is read-only: nothing here mutates the
World. The Verifier's tools take a location and a time, never a person (I1). Any data-source failure
raises ToolError, which the engine turns into `unverifiable` rather than a guess (I5).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from dragonfly.sim.world import World, compass


def mode() -> str:
    return os.environ.get("DRAGONFLY_MODE", "sim")


class ToolError(Exception):
    """Raised when a data source is unavailable. The Verifier turns this into `unverifiable`, never a guess."""


def check_satellite(world: World, lat: float, lon: float) -> dict:
    if mode() == "live":
        return _firms_live(lat, lon)
    hs = world.hotspots_near(lat, lon)
    return {"source": "NASA FIRMS (simulated VIIRS 375 m)", "hotspots_within_5km": len(hs), "nearest": hs[0] if hs else None}


def check_weather(world: World, lat: float, lon: float) -> dict:
    if mode() == "live":
        return _open_meteo_live(lat, lon)
    w = world.weather
    return {
        "source": "Open-Meteo (simulated)",
        "wind_kmh": w["wind_kmh"],
        "wind_from_deg": w["wind_deg"],
        "spread_toward_deg": (w["wind_deg"] + 180) % 360,
        "spread_toward": compass((w["wind_deg"] + 180) % 360),
        "relative_humidity_pct": w["rh"],
        "temp_c": w["temp_c"],
        "fire_weather": _fire_weather(w["rh"], w["wind_kmh"]),
    }


def other_reports_near(world: World, lat: float, lon: float, exclude_report_id: str | None = None) -> dict:
    n = world.reports_near(lat, lon, exclude_id=exclude_report_id)
    return {"source": "incident memory", "other_reports_within_2km_30min": n}


def nearest_station(world: World, lat: float, lon: float) -> dict:
    return {"source": "station registry", **world.nearest_station(lat, lon)}


def all_stations(world: World, lat: float, lon: float) -> dict:
    return {"source": "station registry", "stations_by_distance": world.stations_ranked(lat, lon)}


def helpers_near(world: World, lat: float, lon: float) -> dict:
    return {"source": "opt-in registry", "opted_in_helpers_within_500m": world.helpers_near(lat, lon)}


def people_needing_help_near(world: World, lat: float, lon: float) -> dict:
    return {"source": "opt-in registry", "opted_in_may_need_help_within_3km": world.people_needing_help_near(lat, lon)}


def people_in_path(world: World) -> dict:
    return {
        "source": "projected spread envelope",
        "spread_toward_deg": world.spread_bearing() if world.fire else None,
        "people": world.people_in_path(),
    }


def fleet_status(world: World) -> dict:
    return {"source": "drone fleet telemetry", **world.fleet_status()}


def fire_state(world: World) -> dict:
    """Coarse fire geometry from satellite fusion. Only the response agents (post-verdict) may call this."""
    if not world.fire:
        return {"source": "satellite fusion", "fire": None}
    f = world.fire
    return {
        "source": "satellite fusion",
        "center": {"lat": f["lat"], "lon": f["lon"]},
        "radius_m": round(f["radius_m"]),
        "growth_m_per_min": f["growth_m_per_tick"],
        "spread_toward_deg": world.spread_bearing(),
        "contained": world.contained_tick is not None,
    }


def _fire_weather(rh: float, wind: float) -> str:
    return "extreme" if rh < 20 and wind > 30 else "low" if rh > 50 else "moderate"


# -- live adapters (off by default; kept so the same code runs on real feeds) --


def _firms_live(lat: float, lon: float) -> dict:
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise ToolError("FIRMS_MAP_KEY not set")
    box = f"{lon - 0.1:.3f},{lat - 0.1:.3f},{lon + 0.1:.3f},{lat + 0.1:.3f}"
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/{box}/1"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ToolError(f"FIRMS unavailable: {e}") from e
    rows = [ln for ln in r.text.splitlines()[1:] if ln.strip()]
    return {"source": "NASA FIRMS VIIRS_SNPP_NRT", "hotspots_within_5km": len(rows), "nearest": None}


def _open_meteo_live(lat: float, lon: float) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m"
    )
    try:
        cur = httpx.get(url, timeout=10).json()["current"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise ToolError(f"Open-Meteo unavailable: {e}") from e
    rh, wind, wd = cur["relative_humidity_2m"], cur["wind_speed_10m"], cur["wind_direction_10m"]
    return {
        "source": "Open-Meteo",
        "wind_kmh": wind,
        "wind_from_deg": wd,
        "spread_toward_deg": (wd + 180) % 360,
        "spread_toward": compass((wd + 180) % 360),
        "relative_humidity_pct": rh,
        "temp_c": cur["temperature_2m"],
        "fire_weather": _fire_weather(rh, wind),
    }


# -- registry: name -> (callable, JSON schema for the model) --------------------

_LOC = {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["lat", "lon"]}
_NONE = {"type": "object", "properties": {}}

TOOLS: dict[str, tuple[Callable[..., dict], str, dict[str, Any]]] = {
    "check_satellite": (check_satellite, "Count NASA FIRMS thermal hotspots within 5 km of a point and return the nearest one.", _LOC),
    "check_weather": (check_weather, "Current wind, humidity, temperature and fire-weather class at a point.", _LOC),
    "other_reports_near": (
        other_reports_near,
        "Count independent reports within 2 km and 30 min of a point.",
        {"type": "object", "properties": {**_LOC["properties"], "exclude_report_id": {"type": "string"}}, "required": ["lat", "lon"]},
    ),
    "nearest_station": (nearest_station, "Nearest fire station, distance and ETA.", _LOC),
    "all_stations": (all_stations, "Every fire station ranked by distance with ETA.", _LOC),
    "helpers_near": (helpers_near, "Opted-in neighbors who said they can help, within 500 m.", _LOC),
    "people_needing_help_near": (people_needing_help_near, "Opted-in neighbors who said they may need help leaving, within 3 km.", _LOC),
    "people_in_path": (people_in_path, "People currently inside the projected spread envelope, with bearing from the fire.", _NONE),
    "fleet_status": (fleet_status, "Drone fleet counts by state, base station, distance to fire, drops so far.", _NONE),
    "fire_state": (fire_state, "Fused fire geometry: center, radius, growth rate, spread direction.", _NONE),
}


def call_tool(world: World, name: str, args: dict[str, Any]) -> dict:
    fn, _, schema = TOOLS[name]
    allowed = set(schema.get("properties", {}))
    return fn(world, **{k: v for k, v in args.items() if k in allowed})


def tool_schemas(names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"name": n, "description": TOOLS[n][1], "input_schema": TOOLS[n][2]} for n in names]

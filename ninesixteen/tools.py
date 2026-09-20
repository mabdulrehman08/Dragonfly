"""Tools the agents may call. Each has a `sim` and a `live` behavior behind one flag.

The agents never know which one they're talking to. Every tool is read-only.
The Verifier's tools take a location and a time — never a person.
"""
from __future__ import annotations

import os

import httpx

from ninesixteen.sim.world import World

MODE = os.environ.get("NINESIXTEEN_MODE", "sim")


class ToolError(Exception):
    """Raised when a data source is unavailable. The Verifier turns this into `unverifiable`, never a guess."""


def check_satellite(world: World, lat: float, lon: float) -> dict:
    if MODE == "live":
        return _firms_live(lat, lon)
    hs = world.hotspots_near(lat, lon)
    return {"source": "NASA FIRMS (simulated VIIRS 375 m)", "hotspots_within_5km": len(hs),
            "nearest": hs[0] if hs else None}


def check_weather(world: World, lat: float, lon: float) -> dict:
    if MODE == "live":
        return _open_meteo_live(lat, lon)
    w = world.weather
    return {"source": "Open-Meteo (simulated)", "wind_kmh": w["wind_kmh"], "wind_from_deg": w["wind_deg"],
            "relative_humidity_pct": w["rh"], "temp_c": w["temp_c"],
            "fire_weather": "extreme" if w["rh"] < 20 and w["wind_kmh"] > 30 else "low" if w["rh"] > 50 else "moderate"}


def other_reports_near(world: World, lat: float, lon: float, exclude_report_id: str | None = None) -> dict:
    n = world.reports_near(lat, lon, exclude_id=exclude_report_id)
    return {"source": "incident memory", "other_reports_within_2km_30min": n}


def nearest_station(world: World, lat: float, lon: float) -> dict:
    return world.nearest_station(lat, lon)


def helpers_near(world: World, lat: float, lon: float) -> dict:
    return {"opted_in_helpers_within_500m": world.helpers_near(lat, lon)}


# -- live adapters (off by default; kept so the same code runs on real feeds) --

def _firms_live(lat: float, lon: float) -> dict:
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise ToolError("FIRMS_MAP_KEY not set")
    box = f"{lon-0.1:.3f},{lat-0.1:.3f},{lon+0.1:.3f},{lat+0.1:.3f}"
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/{box}/1"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ToolError(f"FIRMS unavailable: {e}") from e
    rows = [ln for ln in r.text.splitlines()[1:] if ln.strip()]
    return {"source": "NASA FIRMS VIIRS_SNPP_NRT", "hotspots_within_5km": len(rows), "nearest": None}


def _open_meteo_live(lat: float, lon: float) -> dict:
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m")
    try:
        cur = httpx.get(url, timeout=10).json()["current"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise ToolError(f"Open-Meteo unavailable: {e}") from e
    rh, wind = cur["relative_humidity_2m"], cur["wind_speed_10m"]
    return {"source": "Open-Meteo", "wind_kmh": wind, "wind_from_deg": cur["wind_direction_10m"],
            "relative_humidity_pct": rh, "temp_c": cur["temperature_2m"],
            "fire_weather": "extreme" if rh < 20 and wind > 30 else "low" if rh > 50 else "moderate"}

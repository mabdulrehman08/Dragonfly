from ninesixteen.schemas import DronePlan
from ninesixteen.sim.world import World, bearing_deg, compass, dist_m, load_scenario, offset


def test_haversine_and_offset_roundtrip():
    lat, lon = 34.19, -118.11
    lat2, lon2 = offset(lat, lon, 1000, 90)
    assert abs(dist_m(lat, lon, lat2, lon2) - 1000) < 1
    assert abs(bearing_deg(lat, lon, lat2, lon2) - 90) < 0.5
    assert compass(0) == "N" and compass(45) == "NE" and compass(270) == "W"


def test_scenario_runs_and_fires_reports():
    w = World(load_scenario("eaton_baseline"))
    n = 0
    for _ in range(30):
        n += len(w.step())
    assert w.tick == 30 and n == 12
    assert len(w.hotspots) == 3, "satellite drops one hotspot every 10 ticks once the fire is ≥ 100 m"
    assert w.fire["radius_m"] == 40 + 30 * 25


def test_proxy_report_carries_the_neighbours_location():
    w = World(load_scenario("eaton_baseline"))
    reps = []
    for _ in range(5):
        reps += w.step()
    proxy = next(r for r in reps if r.for_whom == "other")
    assert proxy.other_lat is not None and proxy.other_lat != proxy.lat


def test_no_fire_scenario_never_detects_anything():
    w = World(load_scenario("false_alarm_night"))
    for _ in range(15):
        w.step()
    assert w.hotspots == [] and w.fire is None and w.metrics()["people_at_risk"] == 0


def test_drones_only_fly_after_launch_and_contain_the_fire():
    w = World(load_scenario("swarm_500"))
    for _ in range(6):
        w.step()
    assert all(d["state"] == "base" for d in w.drones)
    w.launch(DronePlan(drones=500, target_bearing_deg=45.0, pattern="head_attack"))
    for _ in range(10):
        w.step()
    assert w.drops > 0 and w.contained_tick is not None
    assert w.metrics()["acres_burned"] < w.metrics()["acres_without_drones"]


def test_contacted_people_walk_out_of_the_path():
    w = World(load_scenario("eaton_baseline"))
    for _ in range(6):
        w.step()
    at_risk = [c for c in w.citizens if c["state"] == "in_path"]
    assert at_risk
    for c in at_risk:
        w.mark_contacted(c["id"])
    for _ in range(24):
        w.step()
    assert w.metrics()["people_saved"] >= 1

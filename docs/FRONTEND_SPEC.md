# ninesixteen — frontend spec

Hand this whole file to whoever (or whatever) builds the dashboard. It contains everything the frontend needs:
what the product is, the exact backend API and JSON shapes, what every panel must show, and the hard rules.

## 1. What the app is (one paragraph)

ninesixteen is a neighborhood wildfire emergency layer that sits beside 911. Anyone can report a fire, for themselves
or for a neighbor who can't. Thirteen real Claude agents verify each report against the world (satellite, weather,
other reports), never against the person, and prepare a full response: fire station, evacuation orders, neighbor
helper asks, a public alert, and a drone suppression plan. Nothing is sent and no drone flies until a human dispatcher
clicks **Approve**. After the click, phones light up, opted-in neighbors walk to opted-in neighbors who need help,
people move out of the fire's path, and drones cycle between base and the fire head. The world is a deterministic
simulation; the agents are real. The dashboard is the dispatcher's screen and the demo stage.

Tagline (put it on screen): *Everyone can call for anyone. The machine checks the world, not the person.
A human decides. A neighbor arrives first.*

## 2. Hard rules the frontend must respect

1. **Single file**: `ninesixteen/static/dashboard.html`, plain HTML + CSS + JS. No framework, no build step, no npm.
   External libraries only from a CDN, and only if they load in under a second. SVG map, not Leaflet.
2. **Polling, not sockets**: `GET /state` every 1000 ms. The page is a pure function of that JSON.
3. **There is no dismiss button anywhere.** Incidents are `open` or `approved`. A metric named "dismissed" is shown
   and always reads 0. This is a product rule, not an oversight.
4. **Approve is the only action that sends anything.** Make the button visually heavy. Corroborated incidents show
   "Approve"; human-lane incidents show "Approve anyway".
5. **Never show a reporter credibility score or rating.** Reports are shown verbatim as data.
6. **Projector-readable**: dark theme, 15px+ body text, big metric numbers, high-contrast state colors.
7. Must render correctly at 1440 px wide (projector, three columns) and degrade to one column under 1100 px.

## 3. Backend API (FastAPI, same origin, port 8916)

| method | path | body | returns |
|---|---|---|---|
| GET | `/` | | the dashboard HTML |
| GET | `/state` | | the full world snapshot (below) |
| GET | `/scenarios` | | `["eaton_baseline","false_alarm_night","injection","swarm_500"]` |
| POST | `/reset/{scenario}` | | `{"ok":true,"scenario":"..."}` |
| POST | `/step` | | `{"tick":n,"reports":["R001"]}` advances one simulated minute |
| POST | `/play` | | `{"playing":true|false}` toggles auto-step every 1.5 s |
| POST | `/approve/{incident_id}` | | `{"ok":true,"sent":n,"drones_launched":n,"squads":n}` |

One simulated tick = one minute. Scenarios run 12 to 40 ticks (`ticks` in state). In `claude-code` mode a tick with
reports takes 5 to 15 s of real time because real agents are running; show a "agents working" indicator when
`activity` has grown since the last poll or when the tick counter has not advanced for more than 2 s while `playing`.

## 4. `GET /state` shape

```jsonc
{
  "scenario": "swarm_500",
  "tick": 12, "ticks": 40, "playing": true,
  "scenarios": ["eaton_baseline", "false_alarm_night", "injection", "swarm_500"],

  "fire": { "lat": 34.199, "lon": -118.105, "radius_m": 340.0, "growth_m_per_tick": 25,
            "wind_deg": 225, "wind_kmh": 41, "rh": 11, "temp_c": 29 } | null,   // null = no fire scenario
  "weather": { "wind_deg": 225, "wind_kmh": 41, "rh": 11, "temp_c": 29 },        // wind_deg = where wind blows FROM
  "spread_bearing": 45,        // degrees the fire head moves TOWARD (0=N, 90=E); null when no fire
  "envelope_m": 1050.0,        // how far the head is projected to travel in 30 min

  "citizens": [ { "id": "c01", "name": "Maria", "lat": 34.1912, "lon": -118.1124,
                  "opt_in": "none" | "can_help" | "may_need_help",
                  "state": "safe" | "in_path" | "contacted" | "evacuating" | "evacuated" | "overrun",
                  "home_lat": 34.1912, "home_lon": -118.1124, "mode": "drive" | "walk",
                  "helper_id": "c02" | null, "reported": true } ],                // 20 people

  "stations": [ { "name": "LACoFD Station 11 (Altadena)", "lat": 34.1856, "lon": -118.1108 } ],  // 5
  "hotspots": [ { "lat": 34.197, "lon": -118.107, "tick": 10, "confidence": "high", "source": "SIM-VIIRS" } ],

  "drones": [ { "id": "D001", "lat": 34.1856, "lon": -118.1108,
                "state": "base" | "enroute" | "returning" | "refill", "refill_left": 0 } ],   // up to 500
  "drone_base": { "lat": 34.1856, "lon": -118.1108, "name": "LACoFD Station 11 (Altadena)" },
  "mission": { "drones": 500, "target_bearing_deg": 45.0, "pattern": "head_attack", "rationale": "..." } | null,

  "reports": [ { "id": "R001", "at_tick": 3, "reporter_id": "c01", "lat": 34.1912, "lon": -118.1124,
                 "for_whom": "self" | "other", "other_lat": null, "other_lon": null,
                 "text": "Smoke above Eaton Canyon, orange glow behind the ridge" } ],

  "incidents": [ /* see §5 */ ],

  "outbox": [ { "tick": 6, "incident": "INC-0001", "to": "c01",
                "kind": "reporter" | "helper" | "alert", "text": "..." } ],     // simulated SMS, only after Approve

  "log": [ "t10 satellite: hotspot detected", "t12 R008 merged into INC-0001 (x7, corroborated)" ],  // last 60
  "activity": [ { "tick": 12, "agent": "verifier", "run_id": "c4e17d99-...", "ok": true, "ms": 7687,
                  "cost_usd": 0.00836, "tools": ["check_satellite", "other_reports_near", "check_weather"],
                  "note": "", "label": "corroborated", "report_id": "R008" } ],   // last 80 agent runs

  "metrics": { "reports": 12, "incidents": 2, "corroborated": 1, "human_review": 1, "approved": 1,
               "dismissed": 0, "first_corroborated_tick": 5,
               "people_at_risk": 3, "people_saved": 6, "people_overrun": 0,
               "drones_active": 500, "drops": 500, "contained_tick": 8, "extinguished_tick": 8,
               "acres_burned": 35.9, "acres_without_drones": 839.7,
               "cost_usd": 0.45, "sessions": 55, "failed_runs": 0 }
}
```

## 5. Incident shape

```jsonc
{
  "id": "INC-0001", "lat": 34.1912, "lon": -118.1124,
  "created_tick": 3, "updated_tick": 22, "corroborated_tick": 5 | null, "approved_tick": 6 | null,
  "report_ids": ["R001", "R002", "R003"], "corroboration_count": 11,
  "status": "open" | "approved",            // NOTHING ELSE EXISTS
  "needs_human_review": false,               // true = "Needs your eyes" lane; false + open = "Ready to approve"
  "cost_usd": 0.2033, "sessions": 44,

  "draft": {  // Intake agent
    "report_id": "R001", "lat": ..., "lon": ..., "hazard": "wildfire", "proxy": false,
    "people_at_risk": 1, "needs_help_leaving": true,
    "urgency_signals": ["flames", "embers", "spreading", "panic", "hedged", "injection_suspected"],
    "first_impression": "real" | "unsure" | "doubtful", "reasons": ["..."] },

  "verdict": {  // Verifier agent — it never saw the text or the person
    "label": "corroborated" | "uncorroborated" | "unverifiable", "confidence": 0.97,
    "evidence": [ { "source": "NASA FIRMS (simulated VIIRS 375 m)", "finding": "2 hotspots within 5 km ...", "supports": true },
                  { "source": "incident memory", "finding": "11 other reports within 2 km and 30 min", "supports": true },
                  { "source": "Open-Meteo (simulated)", "finding": "Extreme fire weather ...", "supports": true } ] },

  // everything below is null until the incident is corroborated and the response agents have run
  "plan": { "priority": 1 | 2 | 3, "station": "LACoFD Station 11 (Altadena)", "distance_km": 0.6, "eta_min": 3,
            "evacuation_direction": "SE", "message_to_reporter": "...", "helper_ids": ["c02"], "message_to_helpers": "..." },
  "forecast": { "spread_bearing_deg": 45.0, "rate_class": "slow"|"moderate"|"fast"|"extreme", "envelope_m_30min": 1640.0, "notes": "..." },
  "perimeter": { "lat": ..., "lon": ..., "radius_m": 165.0, "hotspot_count": 0, "confidence": 0.5 },
  "resources": { "station": "...", "apparatus": 3, "eta_min": 3, "mutual_aid": true, "rationale": "..." },
  "evacuation": { "rally_point": "Foothill Community Center", "orders": [ { "person_id": "c08", "direction": "SW", "mode": "drive"|"walk"|"wait_for_helper" } ] },
  "helpers": { "assignments": [ { "helper_id": "c01", "person_id": "c08", "ask_text": "Hi Maria, ... Reply YES and we'll share their address." } ] },
  "notice": { "text": "Wildfire near Eaton Canyon/Altadena: Station 11 is responding. Evacuate to the SOUTHEAST." },
  "drone_plan": { "drones": 500, "target_bearing_deg": 45.0, "pattern": "head_attack"|"flank_attack"|"perimeter_ring", "rationale": "..." },
  "squads": [ { "squad_id": "S01", "waypoint_lat": ..., "waypoint_lon": ..., "action": "drop" } ],   // after Approve
  "after_action": { "summary": "...", "what_worked": ["..."], "what_to_improve": ["..."] } | null,  // end of run

  "runs": [ { "agent": "intake", "run_id": "f3b9cdfe-...", "tick": 3, "model": "claude-sonnet-5",
              "input_tokens": 1421, "output_tokens": 175, "cost_usd": 0.00204, "ok": true, "ms": 4509, "note": "" } ]
}
```

Lane rule: `status == "approved"` → Approved lane. Else `needs_human_review` → "Needs your eyes". Else → "Ready to approve".

## 6. Screens and panels

### Header
Logo text `ninesixteen` (accent on "sixteen"), the tagline, scenario `<select>` (from `scenarios`), buttons
**Reset**, **Step**, **Play/Pause** (label follows `playing`), and a big monospace tick counter `t12/40`.

### Metrics strip (one row of cards, big numbers)
reports · incidents · corroborated · human review · **dismissed (always 0, styled green)** · first corroborated (tick) ·
people at risk (red when >0) · **people saved (green, the hero number)** · overrun (red when >0) ·
drones active · drops · acres burned · acres without drones · cost ($, 3 decimals) · agent sessions · failed runs.
Show a banner when `metrics.contained_tick` is set ("Fire contained at t8, drones outpacing growth") and a stronger
one when `extinguished_tick` is set ("Fire out at t8 after 500 drops · 35.9 acres instead of 839.7").

### Map (SVG, left column, largest panel)
Project lat/lon to the viewBox by fitting all citizens, stations and the fire with padding. Draw, in this order:
1. Spread envelope: a translucent cone from the fire center along `spread_bearing`, length `envelope_m`.
2. Fire: filled circle of `fire.radius_m` in orange-red with a soft glow. Animate radius changes.
3. Hotspots: small amber diamonds (satellite detections).
4. Stations: white squares, name on hover. Drone base: dashed cyan ring around it.
5. Citizens: 6 px dots colored by state — safe grey, reported (still safe) magenta, in_path amber, contacted/evacuating
   light green, evacuated green, overrun red. Ring the dot amber for `may_need_help`, green for `can_help`.
   Hover shows `name · opt_in · state`. When `helper_id` is set, draw a thin line from helper to person.
6. Drones: 2.5 px cyan dots for every drone whose state is not `base`. With 500 drones this must stay smooth:
   build one `<path>` or use a document fragment, not 500 DOM updates through innerHTML.
7. Compass rose top-right showing spread direction and `wind_kmh`.
Legend under the map.

### Dispatcher queue (middle column)
Three lanes with counts: **Ready to approve**, **Needs your eyes**, **Approved**. Each incident is a card:
- Header row: id (mono), verdict pill (corroborated green / uncorroborated amber / unverifiable red), `priority 1`
  pill in red when `plan.priority == 1`, "reported for a neighbor" pill if any report has `for_whom == "other"`,
  "injection suspected" pill if `draft.urgency_signals` contains it, and the **Approve** button on the right
  (or `approved t6` pill when done).
- Confidence bar (`verdict.confidence`).
- Evidence list: one line per `verdict.evidence`, check for `supports`, cross otherwise, source in muted text.
- Meta line: `x{corroboration_count} reports · signals · {sessions} sessions · ${cost_usd}`.
- Reports: expandable list of the verbatim texts with tick and reporter first name (from `citizens`).
- Response block (only when `plan` exists): station + distance + ETA + evacuate direction; message to reporter in
  quotes; helper asks (helper → person names); evacuation orders count and rally point; drone plan (count, pattern,
  bearing); apparatus and mutual aid. After-action summary when present.
- Click **Approve** → `POST /approve/{id}` then re-poll immediately.

### Phones panel (right column, top)
`outbox` newest first as SMS bubbles. Header: recipient name, kind, tick, incident. Color by kind: reporter green,
helper amber, alert red. Empty state text: "no messages sent — nothing goes out without Approve". Animate new
bubbles in.

### Agent activity (right column, middle)
`activity` newest first, monospace: `t12 verifier c4e17d99 → check_satellite, other_reports_near, check_weather
7687ms $0.0084`. Failed runs in red with `note`. Show the verifier's `label` when present. This panel is the proof
that real agents are running; make it visible on a projector.

### World log (right column, bottom)
`log` newest first, monospace.

## 7. Colors and type (suggested tokens)
bg #0b0d10 · panel #14171c · line #262b33 · text #e8eaed · muted #8b93a1 · fire #ff5a1f · ember #ffb347 ·
ok/green #3ddc84 · warn/amber #ffd166 · bad/red #ff4d6d · drone/cyan #5ac8fa · safe-green #7ee787.
System UI font for text, monospace for ids, ticks and logs.

## 8. Demo script the UI must make easy
1. Select `swarm_500`, press Play. Reports appear from t3; agent activity scrolls; incident INC-0001 appears in
   "Needs your eyes" as uncorroborated, then moves to "Ready to approve" at t5 with a full response block.
   A second incident (prank downtown) stays in "Needs your eyes" the whole time.
2. Click Approve around t6. Phones fill with ~11 messages, 500 cyan dots stream from the base to the fire head,
   the contained/extinguished banner appears at t8, people dots turn green as they leave the path, "people saved"
   climbs, "acres burned" freezes far below "acres without drones".
3. Switch to `false_alarm_night`: no fire, two reports, both in "Needs your eyes", dismissed still 0.
4. Switch to `injection`: the injected texts show the "injection suspected" pill and stay uncorroborated until the
   satellite hotspot at t10.

## 9. Files
- Current implementation to replace: `ninesixteen/static/dashboard.html` (about 300 lines; usable as a reference).
- Backend: `ninesixteen/server.py` (routes), `ninesixteen/sim/world.py` (`snapshot()` builds `/state`).
- Run: `NINESIXTEEN_LLM=claude-code uvicorn ninesixteen.server:app --port 8916`, or `NINESIXTEEN_LLM=mock` for a
  free, instant version of the same JSON to develop against.

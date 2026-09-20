"""Typed contracts between the three agents. These are the only things that cross agent boundaries."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Impression = Literal["real", "unsure", "doubtful"]
VerdictLabel = Literal["corroborated", "uncorroborated", "unverifiable"]


class Report(BaseModel):
    id: str
    at_tick: int
    reporter_id: str
    lat: float
    lon: float
    for_whom: Literal["self", "other"] = "self"
    other_lat: float | None = None
    other_lon: float | None = None
    text: str


class IncidentDraft(BaseModel):
    """Agent 1 (Intake) output. Reads the story, structures it."""
    report_id: str
    lat: float
    lon: float
    hazard: str = "wildfire"
    proxy: bool = False
    people_at_risk: int = 0
    needs_help_leaving: bool = False
    urgency_signals: list[str] = Field(default_factory=list)
    first_impression: Impression = "unsure"
    reasons: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    source: str
    finding: str
    supports: bool


class Verdict(BaseModel):
    """Agent 2 (Verifier) output. Describes the evidence, never the person."""
    label: VerdictLabel
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)


class ActionPlan(BaseModel):
    """Agent 3 (Emergency) output. Nothing here is sent until a human approves."""
    priority: int = Field(ge=1, le=3)
    station: str
    distance_km: float
    eta_min: int
    evacuation_direction: str
    message_to_reporter: str
    helper_ids: list[str] = Field(default_factory=list)
    message_to_helpers: str = ""


class Incident(BaseModel):
    id: str
    lat: float
    lon: float
    created_tick: int
    updated_tick: int
    report_ids: list[str]
    corroboration_count: int = 1
    draft: IncidentDraft
    verdict: Verdict
    plan: ActionPlan | None = None
    needs_human_review: bool = True
    status: Literal["open", "approved"] = "open"  # there is deliberately no "dismissed" state
    cost_usd: float = 0.0
    sessions: int = 0

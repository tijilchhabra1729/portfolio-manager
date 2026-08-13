"""The briefing's shared vocabulary — the blackboard the agents reason over.

Three levels of output bubble up the hierarchy: a `SectorFinding` per held sector, a
`MarketBrief` per market, and one `Briefing` for the user. Every level carries the three
things the user asked for — *what happened*, *what it indicates*, and a positive/negative
*direction* — which is the new dimension over the phase-2 insights (those only ever warn).

The models are stored in the graph state as plain dicts (`model_dump()`), so the state
stays trivially mergeable and JSON-serialisable for the `briefings` table; the pydantic
schemas exist to *validate and coerce* the LLM's reply before it becomes a dict. Direction
and significance are coerced to a known value rather than rejected — a slightly-off label
must not throw away an otherwise-good finding (same discipline as `analyst._Insight`).
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, field_validator

# --- the small vocabularies, coerced not rejected ---
POSITIVE, NEGATIVE, NEUTRAL = "positive", "negative", "neutral"
_DIRECTIONS = {POSITIVE, NEGATIVE, NEUTRAL}
HIGH, MEDIUM, LOW = "high", "medium", "low"
_SIGNIFICANCES = {HIGH, MEDIUM, LOW}


def _coerce(value: str, allowed: set[str], default: str) -> str:
    v = (value or "").lower().strip()
    return v if v in allowed else default


# --- what the sector LLM call returns (before we attach market/sector) ---


class SectorReply(BaseModel):
    direction: str = NEUTRAL
    significance: str = MEDIUM
    what_happened: str = ""
    what_it_indicates: str = ""
    tickers: list[str] = []

    @field_validator("direction")
    @classmethod
    def _dir(cls, v: str) -> str:
        return _coerce(v, _DIRECTIONS, NEUTRAL)

    @field_validator("significance")
    @classmethod
    def _sig(cls, v: str) -> str:
        return _coerce(v, _SIGNIFICANCES, MEDIUM)

    @field_validator("tickers", mode="before")
    @classmethod
    def _tk(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        return list(v) if v else []


class MarketReply(BaseModel):
    overall_direction: str = NEUTRAL
    summary: str = ""
    cross_sector_notes: str = ""

    @field_validator("overall_direction")
    @classmethod
    def _dir(cls, v: str) -> str:
        return _coerce(v, _DIRECTIONS, NEUTRAL)


class OrchestratorReply(BaseModel):
    headline: str = ""
    overall_direction: str = NEUTRAL
    summary: str = ""
    positives: list[str] = []
    negatives: list[str] = []

    @field_validator("overall_direction")
    @classmethod
    def _dir(cls, v: str) -> str:
        return _coerce(v, _DIRECTIONS, NEUTRAL)

    @field_validator("positives", "negatives", mode="before")
    @classmethod
    def _lst(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v] if v else []


# --- what travels up the graph (the carried-through, validated shapes) ---


class SectorFinding(BaseModel):
    market: str
    sector: str
    direction: str
    significance: str
    what_happened: str
    what_it_indicates: str
    tickers: list[str] = []


class MarketBrief(BaseModel):
    market: str
    overall_direction: str
    summary: str
    cross_sector_notes: str = ""
    sectors: list[SectorFinding] = []


class Briefing(BaseModel):
    headline: str
    overall_direction: str
    summary: str
    positives: list[str] = []
    negatives: list[str] = []
    markets: list[MarketBrief] = []
    generated_by: str = "rules"


# --- the task a sector agent is dispatched with (one Send payload) ---


@dataclass(frozen=True)
class SectorTask:
    market: str          # "INDIA" | "US"
    currency: str
    sector: str
    holdings: tuple[dict, ...]  # {ticker, name, allocation_pct, pnl_pct, cap_class}

    def as_payload(self) -> dict:
        return {
            "market": self.market,
            "currency": self.currency,
            "sector": self.sector,
            "holdings": list(self.holdings),
        }


class BriefingState(TypedDict, total=False):
    """The LangGraph blackboard. The three `Annotated[..., operator.add]` channels are
    what let the parallel sector and market nodes append concurrently without clobbering
    each other; everything else is read-only context set once at the start."""

    user_id: str
    plan: str
    sector_tasks: list[dict]        # Send payloads, one per held sector (both markets)
    market_ctx: dict               # market -> {currency, direction hint, flags, totals}
    sector_findings: Annotated[list[dict], operator.add]
    market_briefs: Annotated[list[dict], operator.add]
    briefing: dict

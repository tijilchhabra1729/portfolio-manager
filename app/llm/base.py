"""The LLM seam.

Everything above this asks for one thing — "given this system prompt and this payload,
return a JSON object" — and never learns whether Groq or Claude answered. Swapping or
adding a provider is one class, exactly like `MarketDataProvider` in the market layer.

Two providers exist: Groq (free tier, an OpenAI-shaped endpoint) and Claude (premium).
The runner picks per the user's plan; if neither key is set, the LLM features simply say
so and the rule agents carry the dashboard on their own.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

log = logging.getLogger(__name__)


class AnalystError(Exception):
    """The model was unreachable or never produced usable JSON. Callers log and skip —
    an LLM failure must never break a refresh or a dashboard load."""


class AnalystModel(Protocol):
    # The label that ends up on an insight's source chip, so the UI can show which model
    # produced a card ("groq" / "claude").
    name: str

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500) -> dict[str, Any]:
        """Return the model's reply parsed as a single JSON object.

        Implementations must raise AnalystError (never return junk) if the model is
        unreachable or its output can't be coerced to an object.
        """
        ...


def extract_json_object(text: str) -> dict[str, Any]:
    """Coerce a model's reply into a JSON object.

    Models wrap JSON in prose or ```json fences even when told not to. Try a straight
    parse, then the fenced block, then the first balanced {...} span. Raise rather than
    return a half-parsed guess -- the caller validates against a schema next, and feeding
    it garbage just moves the failure downstream.
    """
    text = (text or "").strip()
    if not text:
        raise AnalystError("empty model response")

    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
        # A bare array is valid JSON but not the object shape every caller expects;
        # wrap it so callers can read a predictable key.
        if isinstance(parsed, list):
            return {"items": parsed}

    raise AnalystError("model response contained no JSON object")


def _json_candidates(text: str):
    yield text
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        yield fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        yield text[start : end + 1]

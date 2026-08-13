"""Which model runs, given a plan and the keys on hand.

The rule: premium users get Claude *if* an Anthropic key is configured; everyone else
(and premium with no Anthropic key) gets a free provider — Gemini if its key is set
(preferred: its free tokens-per-minute budget dwarfs Groq's, which matters for the
fan-out briefing), otherwise Groq. If no key exists at all, there is no analyst and the
caller falls back to rules or a "set a key" message. One place decides this so every LLM
feature — the news analyst, Explore, the weekly report, the briefing — tiers identically.
"""

from __future__ import annotations

from app.config import settings
from app.llm.base import AnalystModel
from app.llm.claude import ClaudeModel
from app.llm.openai_compat import OpenAICompatModel

PREMIUM = "premium"


def select_model(plan: str) -> AnalystModel | None:
    cfg = settings()

    if plan == PREMIUM and cfg.anthropic_api_key:
        return ClaudeModel(cfg.anthropic_api_key, cfg.anthropic_model)

    # Everyone else — and premium with no Claude key — gets a free provider. Prefer Gemini
    # (its free TPM budget comfortably absorbs the briefing's fan-out); fall back to Groq.
    if cfg.gemini_api_key:
        return OpenAICompatModel(
            cfg.gemini_api_key, cfg.gemini_model, cfg.gemini_base_url, name="gemini",
            # The free flash alias is a thinking model; cap the reasoning so the token
            # budget goes to the answer, not hidden thinking (empty-content otherwise).
            extra_body={"reasoning_effort": "low"},
        )
    if cfg.groq_api_key:
        return OpenAICompatModel(cfg.groq_api_key, cfg.groq_model, cfg.groq_base_url, name="groq")

    return None


def any_llm_configured() -> bool:
    cfg = settings()
    return bool(cfg.groq_api_key or cfg.gemini_api_key or cfg.anthropic_api_key)

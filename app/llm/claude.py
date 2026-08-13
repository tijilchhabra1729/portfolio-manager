"""Claude, the premium provider.

Uses the official Anthropic SDK's `messages.create`. Thinking is left off: these are
bounded JSON-extraction tasks, not open-ended reasoning, so adaptive thinking would only
add latency and complicate pulling the text back out. The same defensive JSON parser as
the Groq path runs on the reply, so both providers behave identically to their callers.
"""

from __future__ import annotations

import logging

import anthropic

from app.llm.base import AnalystError, extract_json_object

log = logging.getLogger(__name__)

# The reply must be a single JSON object. Claude honours a firm instruction well, and the
# base parser strips any stray fence or preamble that slips through anyway.
_JSON_GUARD = "\n\nRespond with a single JSON object and nothing else — no prose, no markdown fences."


class ClaudeModel:
    name = "claude"

    def __init__(self, api_key: str, model: str, *, timeout: float = 60.0) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500) -> dict:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system + _JSON_GUARD,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            raise AnalystError(f"claude error: {exc}") from exc

        if response.stop_reason == "refusal":
            raise AnalystError("claude refused the request")

        text = "".join(block.text for block in response.content if block.type == "text")
        return extract_json_object(text)

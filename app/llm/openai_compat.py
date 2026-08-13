"""Groq (and any OpenAI-shaped chat endpoint).

Groq serves Llama 3.3 70B over the OpenAI Chat Completions wire format, so this needs no
SDK -- one httpx POST against the endpoint we already depend on. `response_format:
{type: "json_object"}` is Groq's JSON mode; the base parser still runs afterward because
JSON mode guarantees valid JSON, not the *shape* the caller wants.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.llm.base import AnalystError, extract_json_object

log = logging.getLogger(__name__)


class OpenAICompatModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        name: str = "groq",
        timeout: float = 40.0,
        max_429_retries: int = 2,
        max_backoff: float = 8.0,
        extra_body: dict | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.timeout = timeout
        # Provider-specific request fields merged into every payload. Gemini's flash alias
        # is a *thinking* model that otherwise spends the whole max_tokens budget on hidden
        # reasoning and returns empty content, so we send reasoning_effort="low"; Groq sends
        # nothing extra.
        self.extra_body = extra_body or {}
        # Groq's free tier is capped on tokens-per-minute, and a fanned-out job (the
        # briefing fires one call per held sector plus the market and orchestrator calls)
        # can burst past it. A 429 there says exactly how long to wait, so honour it — up to
        # a cap — rather than immediately falling back to a deterministic read. Bounded so a
        # rate-limited run degrades gracefully instead of hanging.
        self.max_429_retries = max_429_retries
        self.max_backoff = max_backoff

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            # No temperature knob exposed: these are extraction tasks, not creative ones,
            # and the default already gives stable structured output.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **self.extra_body,
        }

        for attempt in range(self.max_429_retries + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=self.timeout,
                )
            except httpx.HTTPError as exc:
                raise AnalystError(f"{self.name} unreachable: {exc}") from exc

            if response.status_code == 429 and attempt < self.max_429_retries:
                wait = self._retry_after(response)
                if wait <= self.max_backoff:
                    log.info("%s rate-limited; retrying in %.1fs", self.name, wait)
                    time.sleep(wait)
                    continue
                # The suggested wait is longer than we'll hold a request for; give up now
                # and let the caller fall back rather than block for tens of seconds.
                break

            if response.status_code != 200:
                # 401 (bad key), an over-cap 429, etc.; the caller treats every AnalystError
                # the same -- log and fall back to rules.
                raise AnalystError(f"{self.name} returned {response.status_code}: {response.text[:200]}")

            try:
                choice = response.json()["choices"][0]
                content = choice["message"].get("content")
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise AnalystError(f"{self.name} response was malformed: {exc}") from exc

            if not content:
                # A thinking model that hit the token cap before emitting any answer returns
                # empty content with finish_reason "length". Say so, so the fix (more tokens
                # or less reasoning) is obvious, and let the caller retry / fall back.
                raise AnalystError(
                    f"{self.name} returned no content (finish_reason={choice.get('finish_reason')})"
                )
            return extract_json_object(content)

        raise AnalystError(f"{self.name} rate-limited (429) after {self.max_429_retries} retries")

    def _retry_after(self, response: httpx.Response) -> float:
        """Seconds to wait, from the Retry-After header if present, else a short default."""
        header = response.headers.get("retry-after")
        if header:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass
        return 2.0

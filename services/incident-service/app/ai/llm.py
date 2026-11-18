from __future__ import annotations

import httpx

from ..config import Settings


class LLMUnavailable(Exception):
    pass


DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}


class LLMClient:
    """Minimal provider adapters over plain HTTPS (no SDK): Anthropic Messages API or any OpenAI-compatible
    /chat/completions endpoint (OpenAI, Azure-compatible gateways, Ollama, vLLM, LM Studio...)."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self.client = client or httpx.AsyncClient(timeout=settings.llm_timeout_s)
        self.provider = settings.llm_provider
        self.model = settings.llm_model or DEFAULT_MODELS.get(self.provider, "")

    @property
    def enabled(self) -> bool:
        if self.provider == "anthropic":
            return bool(self.s.llm_api_key)
        if self.provider == "openai":
            return bool(self.s.llm_api_key or self.s.llm_base_url)  # local servers need no key
        return False

    async def complete(self, system: str, user: str) -> str:
        if not self.enabled:
            raise LLMUnavailable("LLM disabled or not configured (LLM_PROVIDER / LLM_API_KEY)")
        try:
            if self.provider == "anthropic":
                base = self.s.llm_base_url or "https://api.anthropic.com"
                r = await self.client.post(f"{base.rstrip('/')}/v1/messages", headers={
                    "x-api-key": self.s.llm_api_key, "anthropic-version": "2023-06-01"},
                    json={"model": self.model, "max_tokens": 2000, "system": system,
                          "messages": [{"role": "user", "content": user}]})
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
            base = self.s.llm_base_url or "https://api.openai.com/v1"
            headers = {"Authorization": f"Bearer {self.s.llm_api_key}"} if self.s.llm_api_key else {}
            r = await self.client.post(f"{base.rstrip('/')}/chat/completions", headers=headers, json={
                "model": self.model, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMUnavailable(f"LLM request failed: {type(exc).__name__}") from exc

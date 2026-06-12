"""AgentRuntime — the only file that imports the agent framework (Hive).

Wraps chiruu12/Hive agents behind a tiny request-response surface so the
rest of the agent layer never touches framework APIs (Agno escape hatch:
swap the internals here, nothing else changes).

Provider routing:
- model_tier="fast"    → Groq (settings.groq_model), hot agent loop
- model_tier="quality" → Anthropic (settings.anthropic_model), visible reasoning

Fallback chain on any provider error: Groq → Anthropic → OpenAI →
deterministic template mode. Providers without an API key configured are
skipped. `settings.agent_llm == "off"` short-circuits straight to template
mode (no framework objects are ever built, no network is touched).

Template-mode convention: callers pass their deterministic, rule-computed
result in `context["fallback"]` (a str, or an instance of `output_schema`).
Template mode returns it verbatim — the full cascade works with zero LLM
access.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from app.settings import settings

logger = logging.getLogger(__name__)

ModelTier = Literal["fast", "quality"]


class AgentRuntime:
    def __init__(
        self,
        name: str,
        instructions: str,
        tools: list[Callable[..., Any]],
        model_tier: ModelTier,
        output_schema: type[BaseModel] | None = None,
    ) -> None:
        self.name = name
        self.instructions = instructions
        self.tools = tools
        self.model_tier = model_tier
        self.output_schema = output_schema
        self._chain: list[tuple[str, Any]] | None = None  # [(provider_name, hive.Agent)]

    # ── public ────────────────────────────────────────────────────────────

    async def run(self, prompt: str, context: dict[str, Any]) -> BaseModel | str:
        """One request-response turn. Falls back across providers, then to template."""
        if settings.agent_llm == "off":
            return self._template(prompt, context)

        ctx_str = self._context_str(context)
        for provider_name, agent in self._agents():
            try:
                if self.output_schema is not None:
                    result = await agent.run_once_structured(
                        prompt, output_type=self.output_schema, context=ctx_str
                    )
                else:
                    result = await agent.run_once(prompt, context=ctx_str)
                return result
            except Exception:
                logger.warning(
                    "agent runtime %s: provider %s failed, falling back",
                    self.name,
                    provider_name,
                    exc_info=True,
                )
        logger.warning("agent runtime %s: all providers failed, using template mode", self.name)
        return self._template(prompt, context)

    # ── internals (all framework imports live below) ──────────────────────

    def _agents(self) -> list[tuple[str, Any]]:
        if self._chain is not None:
            return self._chain

        from hive import Agent, Anthropic, Groq, OpenAI, collect_tools

        providers: list[tuple[str, Any]] = []
        groq = ("groq", lambda: Groq(model=settings.groq_model, api_key=settings.groq_api_key))
        anthropic = (
            "anthropic",
            lambda: Anthropic(model=settings.anthropic_model, api_key=settings.anthropic_api_key),
        )
        openai = (
            "openai",
            lambda: OpenAI(model=settings.openai_model, api_key=settings.openai_api_key),
        )
        order = (
            [groq, anthropic, openai] if self.model_tier == "fast" else [anthropic, groq, openai]
        )
        keys = {
            "groq": settings.groq_api_key,
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
        }

        tools = collect_tools(*self.tools) if self.tools else None
        for provider_name, factory in order:
            if not keys[provider_name]:
                continue  # no key → skip, don't burn a round-trip on a 401
            try:
                providers.append(
                    (
                        provider_name,
                        Agent(
                            name=self.name,
                            model=factory(),
                            instructions=self.instructions,
                            tools=tools,
                        ),
                    )
                )
            except Exception:
                logger.warning(
                    "agent runtime %s: could not build provider %s", self.name, provider_name
                )
        self._chain = providers
        return providers

    def _template(self, prompt: str, context: dict[str, Any]) -> BaseModel | str:
        fallback = (context or {}).get("fallback")
        if fallback is not None:
            return fallback
        return f"[{self.name}] rule-mode response to: {prompt}"

    @staticmethod
    def _context_str(context: dict[str, Any]) -> str | None:
        data = {k: v for k, v in (context or {}).items() if k != "fallback"}
        if not data:
            return None
        return "Live operational context:\n" + json.dumps(data, default=str, indent=2)

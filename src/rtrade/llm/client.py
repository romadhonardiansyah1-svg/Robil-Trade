"""LLM client -- structured output via LiteLLM library mode (PLAN 8.9.1).

Wraps litellm.acompletion() with:
- Structured JSON output (response_format)
- Retry 1x on invalid JSON parse
- Configurable timeout (default 45s total pipeline)
- Token counting + cost logging per call
- Fallback chain handled by LiteLLM router

The application code calls this client with model ALIASES (trading-analyst,
trading-critic, trading-backup). LiteLLM resolves them to actual providers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rtrade.llm.auth.base import CredentialProvider

import litellm
import structlog
from pydantic import BaseModel

from rtrade.core.errors import LLMOutputError, LLMUnavailableError

logger = structlog.get_logger(__name__)

# Suppress litellm's verbose logging.
litellm.suppress_debug_info = True


@dataclass(frozen=True, slots=True)
class LLMCallResult:
    """Result from a single LLM call."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float


@dataclass
class LLMClient:
    """LiteLLM-based LLM client with structured output and retry.

    Uses library mode (no separate proxy process). Model aliases are
    resolved by litellm's router based on config/litellm.yaml.
    """

    api_key: str = ""
    timeout: int = 45
    max_retries: int = 1
    temperature: float = 0.2
    credential_provider: CredentialProvider | None = None
    _call_count: int = field(default=0, init=False, repr=False)
    _total_cost: float = field(default=0.0, init=False, repr=False)
    _total_tokens: int = field(default=0, init=False, repr=False)

    async def complete(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> LLMCallResult:
        """Call LLM with optional structured output.

        Args:
            model: LiteLLM model alias (e.g. 'gemini/gemini-3.1-flash-lite')
            system_prompt: System message content.
            user_prompt: User message content.
            response_schema: If provided, request structured JSON output
                matching this Pydantic model.
            temperature: Override default temperature.

        Returns:
            LLMCallResult with parsed content and usage stats.

        Raises:
            LLMOutputError: If JSON output is invalid after retries.
            LLMUnavailableError: If all providers fail.
        """
        temp = temperature if temperature is not None else self.temperature
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "timeout": self.timeout,
        }

        if self.credential_provider is not None:
            material = await self.credential_provider.resolve()
            material.merge_into(kwargs)
        elif self.api_key:
            kwargs["api_key"] = self.api_key

        # Request structured JSON output if schema provided.
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_object",
            }
            # Add schema hint to system prompt.
            schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
            messages[0]["content"] += (
                f"\n\nYou MUST respond with valid JSON matching this schema:\n"
                f"```json\n{schema_json}\n```"
            )

        last_error: Exception | None = None
        attempts = 1 + self.max_retries

        for attempt in range(attempts):
            try:
                start = time.monotonic()
                response = await litellm.acompletion(**kwargs)
                latency = (time.monotonic() - start) * 1000

                content = response.choices[0].message.content or ""
                usage = response.usage or _empty_usage()

                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                total_tokens = prompt_tokens + completion_tokens

                # Estimate cost from litellm.
                cost = _estimate_cost(model, prompt_tokens, completion_tokens)

                result = LLMCallResult(
                    content=content,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost,
                    latency_ms=latency,
                )

                # Validate JSON if schema was requested.
                if response_schema is not None:
                    _validate_json(content, response_schema)

                self._call_count += 1
                self._total_cost += cost
                self._total_tokens += total_tokens

                logger.info(
                    "llm call completed",
                    model=model,
                    attempt=attempt + 1,
                    tokens=total_tokens,
                    cost_usd=f"{cost:.4f}",
                    latency_ms=f"{latency:.0f}",
                )

                return result

            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "llm json parse failed, retrying",
                    model=model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                continue

            except Exception as exc:
                last_error = exc
                logger.error(
                    "llm call failed",
                    model=model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt < attempts - 1:
                    continue
                raise LLMUnavailableError(f"all LLM attempts failed for {model}: {exc}") from exc

        raise LLMOutputError(f"invalid LLM output after {attempts} attempts: {last_error}")

    def parse_response(self, result: LLMCallResult, schema: type[BaseModel]) -> BaseModel:
        """Parse LLM response content into a Pydantic model."""
        return _validate_json(result.content, schema)

    @property
    def stats(self) -> dict[str, Any]:
        """Return cumulative usage statistics."""
        return {
            "call_count": self._call_count,
            "total_cost_usd": round(self._total_cost, 4),
            "total_tokens": self._total_tokens,
        }


def _validate_json(content: str, schema: type[BaseModel]) -> BaseModel:
    """Parse JSON content and validate against Pydantic schema."""
    # Strip markdown code fences if present.
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last line (fences).
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    return schema.model_validate(data)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Cost estimate from token counts (USD)."""
    try:
        from litellm import cost_per_token

        input_cost, output_cost = cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return float(input_cost) + float(output_cost)
    except Exception:
        # Fallback: Gemini Flash-Lite pricing (~$0.075/1M in, $0.30/1M out).
        input_cost = prompt_tokens * 0.000000075
        output_cost = completion_tokens * 0.0000003
        return input_cost + output_cost


class _EmptyUsage:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0


def _empty_usage() -> _EmptyUsage:
    return _EmptyUsage()

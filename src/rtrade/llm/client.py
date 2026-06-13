"""LLM client -- structured output via LiteLLM library mode (PLAN 8.9.1).

Wraps litellm.acompletion() with:
- Structured JSON output (response_format)
- Retry 1x on invalid JSON parse
- Configurable timeout (default 45s total pipeline)
- Token counting + cost logging per call
- Fallback chain handled by LiteLLM router
- A8: credential_pool for multi-credential fallback with cooldown
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
from rtrade.llm.auth.pool import (
    AllCredentialsExhaustedError,
    CredentialPool,
    classify_llm_error,
    translate_model,
)

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
    A8: credential_pool for multi-credential fallback.
    """

    api_key: str = ""
    timeout: int = 45
    max_retries: int = 1
    temperature: float = 0.2
    credential_provider: CredentialProvider | None = None
    credential_pool: CredentialPool | None = None
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

        Bila credential_pool diset: gagal rate-limit/auth pada satu kredensial →
        kredensial itu cooldown dan panggilan pindah ke kredensial berikutnya.
        """
        temp = temperature if temperature is not None else self.temperature
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        base_kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": temp,
            "timeout": self.timeout,
        }
        if response_schema is not None:
            base_kwargs["response_format"] = {"type": "json_object"}
            schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
            messages[0]["content"] += (
                f"\n\nYou MUST respond with valid JSON matching this schema:\n"
                f"```json\n{schema_json}\n```"
            )

        # --- jalur lama (tanpa pool) — TIDAK berubah perilakunya ---
        if self.credential_pool is None:
            kwargs = dict(base_kwargs)
            kwargs["model"] = model
            if self.credential_provider is not None:
                material = await self.credential_provider.resolve()
                material.merge_into(kwargs)
            elif self.api_key:
                kwargs["api_key"] = self.api_key
            return await self._attempt_loop(kwargs, model, response_schema)

        # --- jalur pool: fallback antar kredensial ---
        pool = self.credential_pool
        tried: set[str] = set()
        last_error: Exception | None = None
        while True:
            try:
                cred = await pool.acquire(exclude=tried)
            except AllCredentialsExhaustedError as exc:
                raise LLMUnavailableError(
                    f"semua kredensial gagal/cooldown untuk {model}: {last_error}"
                ) from exc
            tried.add(cred.cred_id)

            actual_model = translate_model(model, cred.flavor)
            if actual_model is None:
                logger.debug(
                    "credential flavor tidak kompatibel — skip",
                    cred_id=cred.cred_id,
                    flavor=cred.flavor,
                    model=model,
                )
                continue

            kwargs = dict(base_kwargs)
            kwargs["model"] = actual_model
            try:
                material = await cred.credential.resolve()
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "credential resolve gagal — cooldown & lanjut",
                    cred_id=cred.cred_id,
                    error=str(exc),
                )
                await pool.report_failure(cred.cred_id, kind="auth")
                continue
            material.merge_into(kwargs)

            try:
                return await self._attempt_loop(kwargs, actual_model, response_schema)
            except LLMUnavailableError as exc:
                cause = exc.__cause__ or exc
                kind = classify_llm_error(cause)
                last_error = exc
                if kind in ("rate_limit", "auth"):
                    logger.warning(
                        "credential kena limit/auth — fallback ke berikutnya",
                        cred_id=cred.cred_id,
                        kind=kind,
                    )
                    await pool.report_failure(cred.cred_id, kind=kind)
                    continue
                raise

    async def _attempt_loop(
        self,
        kwargs: dict[str, Any],
        model: str,
        response_schema: type[BaseModel] | None,
    ) -> LLMCallResult:
        """Loop retry untuk SATU kredensial (perilaku lama complete())."""
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

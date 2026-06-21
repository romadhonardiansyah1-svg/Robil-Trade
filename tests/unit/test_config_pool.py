"""PoolSettings (llm.pool) config tests — adaptive cooldown core.

Backward compatibility: a config WITHOUT an `llm.pool` block must still
validate (default_factory). Invalid values (0 or > 21600) must be rejected.
"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from rtrade.core.config import LLMSettings, PoolSettings

VALID_LLM = {
    "enabled": False,
    "analyst_model": "gemini/gemini-2.5-flash",
    "critic_model": "gemini/gemini-2.5-flash",
    "temperature": 0.2,
    "max_confidence_adjust": 0.1,
    "timeout_seconds": 30,
}


class TestPoolSettingsDefaults:
    def test_defaults(self) -> None:
        pool = PoolSettings()
        assert pool.cooldown_seconds == 60
        assert pool.auth_cooldown_seconds == 300
        assert pool.subscription_cooldown_seconds == 18000

    def test_llm_without_pool_block_validates_with_default(self) -> None:
        llm = LLMSettings.model_validate(VALID_LLM)
        assert isinstance(llm.pool, PoolSettings)
        assert llm.pool.cooldown_seconds == 60
        assert llm.pool.subscription_cooldown_seconds == 18000

    def test_llm_with_pool_block(self) -> None:
        llm = LLMSettings.model_validate(
            {
                **VALID_LLM,
                "pool": {
                    "cooldown_seconds": 90,
                    "auth_cooldown_seconds": 600,
                    "subscription_cooldown_seconds": 21600,
                },
            }
        )
        assert llm.pool.cooldown_seconds == 90
        assert llm.pool.auth_cooldown_seconds == 600
        assert llm.pool.subscription_cooldown_seconds == 21600


class TestPoolSettingsValidation:
    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PoolSettings(cooldown_seconds=0)

    def test_above_cap_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PoolSettings(subscription_cooldown_seconds=21601)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PoolSettings(auth_cooldown_seconds=-1)

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PoolSettings.model_validate({"cooldown_secondz": 60})

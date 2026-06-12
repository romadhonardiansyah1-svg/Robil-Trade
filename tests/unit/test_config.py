"""Config loader tests — guardrail floors must be impossible to weaken via YAML."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rtrade.core.config import (
    AppConfig,
    EdgeQualitySettings,
    InstrumentsFile,
    RiskSettings,
    Secrets,
    Settings,
)
from rtrade.core.constants import Market, Timeframe
from rtrade.core.errors import ConfigError

VALID_RISK = {
    "risk_per_trade_pct": 1.0,
    "rr_min": 1.5,
    "rr_target": 2.0,
    "sl_atr_min": 0.5,
    "sl_atr_max": 3.0,
    "news_blackout_before_min": 30,
    "news_blackout_after_min": 15,
    "expectancy_guard_window": 30,
}


class TestRealConfigFiles:
    """The committed config/ directory must always load cleanly."""

    def test_load(self, config_dir: Path) -> None:
        cfg = AppConfig.load(config_dir=config_dir, env_file=None)
        assert cfg.settings.signal.confluence_min_score == 60
        assert cfg.settings.signal.confidence_min == 0.55
        assert cfg.settings.signal.edge_quality.enabled is True
        assert cfg.settings.signal.edge_quality.min_score == 65
        assert cfg.settings.risk.rr_min == 1.5
        assert cfg.settings.risk.sl_atr_max == 3.0
        assert cfg.settings.llm.enabled is False  # P1: no LLM yet
        assert cfg.settings.backtest.min_trades_for_validation == 100

    def test_instruments(self, config_dir: Path) -> None:
        cfg = AppConfig.load(config_dir=config_dir, env_file=None)
        assert len(cfg.instruments) == 6  # P1: 3 + P3: 3
        xau = cfg.instrument("XAUUSD")
        assert xau.market == Market.METALS
        assert xau.provider == "twelvedata"
        assert Timeframe.H1 in xau.timeframes
        assert xau.session_filter is True
        btc = cfg.instrument("BTCUSDT")
        assert btc.derivatives is True
        assert btc.session_filter is False

    def test_unknown_instrument_raises(self, config_dir: Path) -> None:
        cfg = AppConfig.load(config_dir=config_dir, env_file=None)
        with pytest.raises(ConfigError, match="unknown instrument"):
            cfg.instrument("DOGEUSD")

    def test_missing_dir_raises_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            AppConfig.load(config_dir=tmp_path, env_file=None)


class TestGuardrailFloors:
    """YAML must NOT be able to weaken hard guardrails (PLAN §2, §8.8)."""

    def test_rr_min_below_floor_rejected(self) -> None:  # GR-03
        with pytest.raises(ValidationError):
            RiskSettings.model_validate({**VALID_RISK, "rr_min": 1.2})

    def test_sl_atr_max_above_cap_rejected(self) -> None:  # GR-04
        with pytest.raises(ValidationError):
            RiskSettings.model_validate({**VALID_RISK, "sl_atr_max": 5.0})

    def test_risk_pct_above_cap_rejected(self) -> None:  # GR-05
        with pytest.raises(ValidationError):
            RiskSettings.model_validate({**VALID_RISK, "risk_per_trade_pct": 3.0})

    def test_rr_target_below_rr_min_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskSettings.model_validate({**VALID_RISK, "rr_target": 1.5, "rr_min": 2.0})

    def test_sl_bounds_inverted_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RiskSettings.model_validate({**VALID_RISK, "sl_atr_min": 3.0, "sl_atr_max": 2.0})

    def test_edge_quality_atr_percentiles_inverted_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min_atr_percentile"):
            EdgeQualitySettings.model_validate(
                {
                    "enabled": True,
                    "min_score": 65,
                    "max_spread_atr": 0.12,
                    "min_atr_percentile": 98,
                    "max_atr_percentile": 96,
                    "max_opposing_wick_ratio": 0.62,
                    "max_total_wick_body_ratio": 6,
                    "min_body_atr": 0.03,
                    "min_volume_ratio": 0.55,
                    "volume_window": 20,
                    "max_range_expansion_atr": 2.8,
                    "max_entry_distance_atr": 1.25,
                }
            )


class TestTypoProtection:
    def test_unknown_settings_key_rejected(self, config_dir: Path) -> None:
        import yaml

        doc = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
        doc["signal"]["confluence_min_scor"] = 60  # typo
        with pytest.raises(ValidationError):
            Settings.model_validate(doc)

    def test_duplicate_instrument_symbol_rejected(self) -> None:
        inst = {
            "symbol": "XAUUSD",
            "market": "metals",
            "provider": "twelvedata",
            "provider_symbol": "XAU/USD",
            "timeframes": ["1h"],
            "context_timeframe": "1d",
            "pip_size": 0.01,
            "quote_currency": "USD",
        }
        with pytest.raises(ValidationError, match="duplicate"):
            InstrumentsFile.model_validate({"instruments": [inst, inst]})


class TestToSEnforcement:
    """PLAN §14.2: consumer OAuth tokens must be rejected at startup."""

    def test_anthropic_oauth_token_rejected(self) -> None:
        with pytest.raises(ValidationError, match="FORBIDDEN"):
            Secrets(_env_file=None, anthropic_api_key_1="sk-ant-oat01-abc123")

    def test_official_api_key_accepted(self) -> None:
        secrets = Secrets(_env_file=None, anthropic_api_key_1="sk-ant-api03-xyz")
        assert secrets.anthropic_api_key_1 == "sk-ant-api03-xyz"

    def test_empty_keys_allowed_in_p0(self) -> None:
        secrets = Secrets(_env_file=None)
        assert secrets.env == "dev"


class TestEquityConfig:
    def test_equity_default_and_override(self, config_dir: Path) -> None:
        cfg = AppConfig.load(config_dir=config_dir, env_file=None)
        # settings.yaml now has equity_usd: 10000
        assert cfg.settings.risk.equity_usd == 10000

    def test_equity_default_fallback(self) -> None:
        # Without equity_usd, the default should be 10000
        risk = RiskSettings.model_validate({**VALID_RISK})
        assert risk.equity_usd == 10_000.0

    def test_equity_custom_value(self) -> None:
        risk = RiskSettings.model_validate({**VALID_RISK, "equity_usd": 25000})
        assert risk.equity_usd == 25000

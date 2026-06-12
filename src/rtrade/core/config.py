"""Configuration loader.

Layout (IMPLEMENTATION_PLAN §7):
- `config/settings.yaml`     — non-secret thresholds (validated, typo-proof).
- `config/instruments.yaml`  — instrument definitions.
- `.env` / process env       — secrets only (via pydantic-settings).

Hard guardrail floors (GR-03/04/05) are enforced HERE at load time: a config
file that tries to weaken them is rejected — the process refuses to start.
All models use `extra="forbid"` so a typo'd key fails loudly instead of being
silently ignored.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rtrade.core.constants import Market, Timeframe
from rtrade.core.errors import ConfigError

# Hard limits from IMPLEMENTATION_PLAN — config may be stricter, never looser.
GR03_RR_MIN_FLOOR = 1.5
GR04_SL_ATR_MAX_CAP = 3.0
GR05_RISK_PCT_CAP = 2.0
LLM_CONFIDENCE_ADJUST_CAP = 0.15

# Consumer-subscription OAuth token prefixes — forbidden per PLAN §14.2 (ToS).
_FORBIDDEN_KEY_PREFIXES = ("sk-ant-oat",)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EdgeQualitySettings(_StrictModel):
    enabled: bool
    min_score: int = Field(ge=0, le=100)
    max_spread_atr: float = Field(gt=0.0, le=1.0)
    min_atr_percentile: float = Field(ge=0.0, le=100.0)
    max_atr_percentile: float = Field(ge=0.0, le=100.0)
    max_opposing_wick_ratio: float = Field(gt=0.0, le=1.0)
    max_total_wick_body_ratio: float = Field(gt=0.0)
    min_body_atr: float = Field(ge=0.0)
    min_volume_ratio: float = Field(ge=0.0)
    volume_window: int = Field(ge=5)
    max_range_expansion_atr: float = Field(gt=0.0)
    max_entry_distance_atr: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _check_consistency(self) -> "EdgeQualitySettings":
        if self.min_atr_percentile >= self.max_atr_percentile:
            raise ValueError("min_atr_percentile must be < max_atr_percentile")
        return self


class SignalSettings(_StrictModel):
    confluence_min_score: int = Field(ge=0, le=100)
    confidence_min: float = Field(ge=0.0, le=1.0)
    max_signals_per_day_per_instrument: int = Field(ge=1)
    price_drift_max_pct: float = Field(gt=0.0)
    candle_staleness_factor: float = Field(ge=1.0)
    edge_quality: EdgeQualitySettings


class RiskSettings(_StrictModel):
    risk_per_trade_pct: float = Field(gt=0.0, le=GR05_RISK_PCT_CAP)
    rr_min: float = Field(ge=GR03_RR_MIN_FLOOR)
    rr_target: float = Field(ge=GR03_RR_MIN_FLOOR)
    sl_atr_min: float = Field(gt=0.0)
    sl_atr_max: float = Field(gt=0.0, le=GR04_SL_ATR_MAX_CAP)
    news_blackout_before_min: int = Field(ge=0)
    news_blackout_after_min: int = Field(ge=0)
    expectancy_guard_window: int = Field(ge=10)
    equity_usd: float = Field(default=10_000.0, gt=0.0)

    @model_validator(mode="after")
    def _check_consistency(self) -> "RiskSettings":
        if self.sl_atr_min >= self.sl_atr_max:
            raise ValueError("sl_atr_min must be < sl_atr_max")
        if self.rr_target < self.rr_min:
            raise ValueError("rr_target must be >= rr_min")
        return self


class LLMSettings(_StrictModel):
    enabled: bool
    analyst_model: str = Field(min_length=1)
    critic_model: str = Field(min_length=1)
    flagship_model: str = Field(default="gemini/gemini-2.5-pro", min_length=1)
    verifier_model: str = Field(default="gemini/gemini-2.5-flash", min_length=1)
    temperature: float = Field(ge=0.0, le=1.0)
    max_confidence_adjust: float = Field(ge=0.0, le=LLM_CONFIDENCE_ADJUST_CAP)
    timeout_seconds: int = Field(ge=5, le=300)
    max_context_tokens: int = Field(default=4000, ge=500, le=32000)


class WalkForwardSettings(_StrictModel):
    train_months: int = Field(ge=6)
    test_months: int = Field(ge=1)
    step_months: int = Field(ge=1)


class BacktestGates(_StrictModel):
    oos_expectancy_after_costs: str
    oos_profit_factor: str
    deflated_sharpe_prob: str
    pbo_max: float = Field(gt=0.0, le=1.0)
    max_drawdown_pct: float = Field(gt=0.0, le=100.0)


class BacktestSettings(_StrictModel):
    min_trades_for_validation: int = Field(ge=100)  # PLAN §8.11.4 — never lower
    walkforward: WalkForwardSettings
    gates: BacktestGates


class Settings(_StrictModel):
    signal: SignalSettings
    risk: RiskSettings
    llm: LLMSettings
    backtest: BacktestSettings


class InstrumentConfig(_StrictModel):
    symbol: str = Field(min_length=3, pattern=r"^[A-Z0-9]+$")
    market: Market
    provider: str = Field(min_length=1)
    provider_symbol: str = Field(min_length=1)
    timeframes: list[Timeframe] = Field(min_length=1)
    context_timeframe: Timeframe
    pip_size: float = Field(gt=0.0)
    quote_currency: str = Field(min_length=3, max_length=5)
    related_currencies: list[str] = Field(default_factory=list)
    session_filter: bool = False
    derivatives: bool = False

    @field_validator("timeframes")
    @classmethod
    def _unique_timeframes(cls, v: list[Timeframe]) -> list[Timeframe]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate timeframes")
        return v


class InstrumentsFile(_StrictModel):
    instruments: list[InstrumentConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_symbols(self) -> "InstrumentsFile":
        symbols = [i.symbol for i in self.instruments]
        if len(set(symbols)) != len(symbols):
            raise ValueError(f"duplicate instrument symbols in instruments.yaml: {symbols}")
        return self


class Secrets(BaseSettings):
    """Secrets from environment / .env only. Never put these in YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://rtrade:rtrade@localhost:5432/rtrade"
    redis_url: str = "redis://localhost:6379/0"

    twelvedata_api_key: str = ""
    finnhub_api_key: str = ""

    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    anthropic_api_key_1: str = ""
    openai_api_key_1: str = ""
    litellm_master_key: str = ""
    litellm_base_url: str = "http://localhost:4000"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    api_auth_token: str = ""

    env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"

    @field_validator(
        "gemini_api_key_1",
        "gemini_api_key_2",
        "anthropic_api_key_1",
        "openai_api_key_1",
    )
    @classmethod
    def _reject_consumer_oauth(cls, v: str) -> str:
        """PLAN §14.2: consumer-subscription OAuth tokens violate provider ToS.

        Only official API keys are allowed anywhere in this system.
        """
        for prefix in _FORBIDDEN_KEY_PREFIXES:
            if v.startswith(prefix):
                raise ValueError(
                    f"consumer OAuth token ({prefix}...) is FORBIDDEN — it violates the "
                    "provider's ToS. Use an official API key (PLAN §14.2)."
                )
        return v


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConfigError(f"{path} must contain a YAML mapping, got {type(doc).__name__}")
    return doc


class AppConfig(BaseModel):
    """Fully-validated application configuration (settings + instruments + secrets)."""

    model_config = ConfigDict(extra="forbid")

    settings: Settings
    instruments: list[InstrumentConfig]
    secrets: Secrets

    @classmethod
    def load(
        cls,
        config_dir: Path | str = Path("config"),
        env_file: Path | str | None = Path(".env"),
    ) -> "AppConfig":
        """Load and validate everything; raise ConfigError on any problem.

        `env_file=None` skips .env (process env still applies) — used by tests.
        """
        config_dir = Path(config_dir)
        try:
            settings = Settings.model_validate(_load_yaml_mapping(config_dir / "settings.yaml"))
            instruments = InstrumentsFile.model_validate(
                _load_yaml_mapping(config_dir / "instruments.yaml")
            ).instruments
            secrets = Secrets(_env_file=env_file)
        except ConfigError:
            raise
        except ValueError as exc:  # pydantic ValidationError subclasses ValueError
            raise ConfigError(f"configuration invalid: {exc}") from exc
        return cls(settings=settings, instruments=instruments, secrets=secrets)

    def instrument(self, symbol: str) -> InstrumentConfig:
        for inst in self.instruments:
            if inst.symbol == symbol:
                return inst
        raise ConfigError(f"unknown instrument: {symbol}")

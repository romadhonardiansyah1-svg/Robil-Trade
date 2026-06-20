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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

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


class CalendarSourceConfig(_StrictModel):
    """Satu sumber kalender dalam rantai composite (FR-CAL-07)."""

    name: str  # "investing" | "nasdaq" | "trading_economics" | "static_high_impact" | "finnhub"
    enabled: bool = True


class CalendarSettings(_StrictModel):
    """Konfigurasi lapisan kalender ekonomi (GR-07b dependency).

    Default fail-CLOSE (fail_open_when_stale=false) — bot tidak pernah trade
    buta terhadap berita. Mem-flip ke true WAJIB keputusan operator eksplisit
    yang di-logging WARNING keras.
    """

    fail_open_when_stale: bool = False
    stale_after_hours: float = Field(default=18.0, gt=0.0)
    sync_lookback_days: int = Field(default=1, ge=0)
    sync_lookforward_days: int = Field(default=7, ge=1)
    sources: list[CalendarSourceConfig] = Field(
        default_factory=lambda: [CalendarSourceConfig(name="static_high_impact", enabled=True)]
    )

    @field_validator("sources")
    @classmethod
    def _unique_source_names(cls, v: list[CalendarSourceConfig]) -> list[CalendarSourceConfig]:
        names = [s.name for s in v]
        if len(set(names)) != len(names):
            raise ValueError(f"calendar.sources names must be unique, got {names}")
        if not v:
            raise ValueError("calendar.sources must not be empty (at least static_high_impact)")
        return v


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


class GateProfile(_StrictModel):
    """Soft (non-floor) signal thresholds, swappable per strategy/timeframe.

    Hard risk floors (rr_min, sl_atr, risk_per_trade_pct, news blackout,
    llm.enabled) are deliberately NOT here — they stay globally validated in
    RiskSettings/LLMSettings. A profile only loosens/tightens the gates that are
    safe to vary between swing and scalping.
    """

    confluence_min_score: int = Field(ge=0, le=100)
    edge_quality_min_score: int = Field(ge=0, le=100)
    confidence_min: float = Field(ge=0.0, le=1.0)
    max_signals_per_day_per_instrument: int = Field(ge=1)


class SignalSettings(_StrictModel):
    confluence_min_score: int = Field(ge=0, le=100)
    confidence_min: float = Field(ge=0.0, le=1.0)
    max_signals_per_day_per_instrument: int = Field(ge=1)
    price_drift_max_pct: float = Field(gt=0.0)
    candle_staleness_factor: float = Field(ge=1.0)
    # P1-7 (G-07): full warmup window required before a scan may emit a signal.
    # Until this many bars exist the scan abstains ("abstain_warmup") instead of
    # acting on under-warmed indicators/regime. Floor 200 keeps the legacy minimum.
    warmup_bars: int = Field(default=500, ge=200)
    edge_quality: EdgeQualitySettings
    # SP-4: named soft-threshold profiles. `default` is auto-synthesized from the
    # global values above when omitted, so configs without a profiles block are
    # byte-compatible. Hard floors are never profileable.
    profiles: dict[str, GateProfile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ensure_default_profile(self) -> "SignalSettings":
        if "default" not in self.profiles:
            self.profiles = {
                **self.profiles,
                "default": GateProfile(
                    confluence_min_score=self.confluence_min_score,
                    edge_quality_min_score=self.edge_quality.min_score,
                    confidence_min=self.confidence_min,
                    max_signals_per_day_per_instrument=self.max_signals_per_day_per_instrument,
                ),
            }
        return self

    def profile(self, name: str) -> GateProfile:
        """Return the named gate profile, falling back to `default`."""
        return self.profiles.get(name, self.profiles["default"])


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
    throttle_enabled: bool = True
    throttle_window: int = Field(default=10, ge=5)
    throttle_mult: float = Field(default=0.5, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def _check_consistency(self) -> "RiskSettings":
        if self.sl_atr_min >= self.sl_atr_max:
            raise ValueError("sl_atr_min must be < sl_atr_max")
        if self.rr_target < self.rr_min:
            raise ValueError("rr_target must be >= rr_min")
        return self


class LLMBudgetSettings(_StrictModel):
    """4-cap LLM budget guard (FR-LLM-09/10/11, G-11)."""

    max_tokens_per_scan: int = Field(default=20000, ge=1)
    max_usd_per_day: float = Field(default=5.0, ge=0.01)
    max_wall_seconds_per_scan: float = Field(default=45.0, ge=1.0)
    max_steps_per_scan: int = Field(default=8, ge=1)


class LLMSettings(_StrictModel):
    enabled: bool
    analyst_model: str = Field(min_length=1)
    critic_model: str = Field(min_length=1)
    flagship_model: str = Field(default="gemini/gemini-2.5-pro", min_length=1)
    temperature: float = Field(ge=0.0, le=1.0)
    max_confidence_adjust: float = Field(ge=0.0, le=LLM_CONFIDENCE_ADJUST_CAP)
    timeout_seconds: int = Field(ge=5, le=300)
    max_context_tokens: int = Field(default=4000, ge=500, le=32000)
    escalation_low: float = Field(default=0.48, ge=0.0, le=1.0)
    escalation_high: float = Field(default=0.63, ge=0.0, le=1.0)
    coroner_enabled: bool = False
    # --- OAuth auth layer (O6) ---
    auth_mode: str = Field(default="api_key")  # api_key|oauth2|vertex|azure_ad
    vertex_project: str = Field(default="")
    vertex_location: str = Field(default="us-central1")
    # --- Per-model auth routing (O11) ---
    default_auth_profile: str = Field(default="")
    auth_profiles: dict[str, Any] = Field(default_factory=dict)
    model_routes: dict[str, Any] = Field(default_factory=dict)
    # --- Budget guard (G-11) ---
    budget: LLMBudgetSettings = Field(default_factory=LLMBudgetSettings)


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
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)


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
    # SP-2 multi-timeframe routing (optional; empty → legacy H1 entry / H4 anchor).
    entry_timeframes: list[Timeframe] = Field(default_factory=list)
    anchor_timeframe: Timeframe | None = None

    @field_validator("timeframes")
    @classmethod
    def _unique_timeframes(cls, v: list[Timeframe]) -> list[Timeframe]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate timeframes")
        return v

    def resolved_entry_timeframes(self) -> list[Timeframe]:
        """Entry timeframes to run the full pipeline on; legacy default = [H1]."""
        return list(self.entry_timeframes) if self.entry_timeframes else [Timeframe.H1]

    def resolved_anchor_timeframe(self) -> Timeframe:
        """Trend/regime anchor timeframe; legacy default = H4."""
        return self.anchor_timeframe if self.anchor_timeframe is not None else Timeframe.H4

    @model_validator(mode="after")
    def _check_mtf(self) -> "InstrumentConfig":
        if self.entry_timeframes:
            if len(set(self.entry_timeframes)) != len(self.entry_timeframes):
                raise ValueError("duplicate entry_timeframes")
            missing = [tf for tf in self.entry_timeframes if tf not in self.timeframes]
            if missing:
                raise ValueError(f"entry_timeframes not in timeframes: {missing}")
        if self.anchor_timeframe is not None:
            if self.anchor_timeframe not in self.timeframes:
                raise ValueError(f"anchor_timeframe not in timeframes: {self.anchor_timeframe}")
            if self.anchor_timeframe in self.entry_timeframes:
                raise ValueError("anchor_timeframe must not also be an entry timeframe")
        return self


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
    twelvedata_api_key_2: str = ""
    twelvedata_api_key_3: str = ""

    oanda_token_1: str = ""
    oanda_token_2: str = ""
    oanda_token_3: str = ""
    oanda_account_1: str = ""
    oanda_account_2: str = ""
    oanda_account_3: str = ""
    oanda_env: Literal["practice", "live"] = "practice"

    finnhub_api_key: str = ""

    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_api_key_4: str = ""
    gemini_api_key_5: str = ""
    anthropic_api_key_1: str = ""
    anthropic_api_key_2: str = ""
    anthropic_api_key_3: str = ""
    openai_api_key_1: str = ""
    openai_api_key_2: str = ""
    openai_api_key_3: str = ""
    xai_api_key_1: str = ""
    xai_api_key_2: str = ""
    xai_api_key_3: str = ""
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
        "gemini_api_key_3",
        "gemini_api_key_4",
        "gemini_api_key_5",
        "anthropic_api_key_1",
        "anthropic_api_key_2",
        "anthropic_api_key_3",
        "openai_api_key_1",
        "openai_api_key_2",
        "openai_api_key_3",
        "xai_api_key_1",
        "xai_api_key_2",
        "xai_api_key_3",
    )
    @classmethod
    def _reject_consumer_oauth(cls, v: str) -> str:
        """Guard: Anthropic consumer-subscription OAuth tokens (sk-ant-oat) forbidden."""
        for prefix in _FORBIDDEN_KEY_PREFIXES:
            if v.startswith(prefix):
                raise ValueError(
                    f"consumer OAuth token ({prefix}...) is FORBIDDEN — use an official API key."
                )
        return v

    def keys_for(self, family: str) -> list[str]:
        """Daftar API key non-kosong untuk satu family provider, urut slot.

        family: "gemini" | "anthropic" | "openai" | "xai"
        """
        slots: dict[str, list[str]] = {
            "gemini": [
                self.gemini_api_key_1,
                self.gemini_api_key_2,
                self.gemini_api_key_3,
                self.gemini_api_key_4,
                self.gemini_api_key_5,
            ],
            "anthropic": [
                self.anthropic_api_key_1,
                self.anthropic_api_key_2,
                self.anthropic_api_key_3,
            ],
            "openai": [
                self.openai_api_key_1,
                self.openai_api_key_2,
                self.openai_api_key_3,
            ],
            "xai": [self.xai_api_key_1, self.xai_api_key_2, self.xai_api_key_3],
        }
        return [k for k in slots.get(family, []) if k]

    def market_keys_for(self, provider: str) -> list[tuple[str, str | None]]:
        """Market-data credential legs for a provider, ordered, empty slots dropped.

        Returns (token, account_or_None) pairs. Mirrors keys_for() for LLM keys.
        """
        if provider == "oanda":
            pairs = [
                (self.oanda_token_1, self.oanda_account_1),
                (self.oanda_token_2, self.oanda_account_2),
                (self.oanda_token_3, self.oanda_account_3),
            ]
            return [(t, a or None) for t, a in pairs if t]
        if provider == "twelvedata":
            keys = [self.twelvedata_api_key, self.twelvedata_api_key_2, self.twelvedata_api_key_3]
            return [(k, None) for k in keys if k]
        return []


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

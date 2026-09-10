from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from SHR_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SHR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://radar:radar@localhost:55432/shanghai_home_radar"
    redis_url: str = "redis://localhost:6379/0"
    collector_queue: str = "collector"
    collector_source: str = "sample_json"
    collector_endpoint: str = "data/sample/example_source_listings.json"
    collector_city: str = "shanghai"
    collector_district: str | None = None
    collector_submarket: str | None = None
    collector_query: str | None = None
    collector_request_timeout_seconds: float = Field(default=20.0, gt=0)
    collector_max_attempts: int = Field(default=4, ge=1, le=10)
    collector_auth_mode: str = "NONE"
    collector_bearer_token: SecretStr | None = None
    collector_basic_username: SecretStr | None = None
    collector_basic_password: SecretStr | None = None
    collector_custom_header_name: str | None = None
    collector_custom_header_value: SecretStr | None = None
    collector_allow_complete_without_coverage: bool = False
    collector_min_complete_items: int = Field(default=1, ge=1)
    collector_min_complete_count_ratio: float = Field(default=0.5, gt=0, le=1)
    collector_partner_csv_max_age_hours: float = Field(default=36.0, gt=0, le=720)
    collector_max_observation_age_hours: float = Field(default=36.0, gt=0, le=720)
    disappearance_final_after_runs: int = Field(default=2, ge=2, le=30)
    scoring_config_path: Path = Path("config/scoring.yaml")
    forecasting_config_path: Path = Path("config/forecasting.yaml")
    future_queue: str = "future"
    decision_config_path: Path = Path("config/decision.yaml")
    validation_config_path: Path = Path("config/validation.yaml")
    source_commit: str = "unknown"
    source_tree_hash: str = "unknown"
    validation_report_directory: Path = Path("data/exports/validation")
    decision_queue: str = "decision"
    daily_report_directory: Path = Path("data/exports/daily")
    daily_run_lock_ttl_seconds: int = Field(default=21600, ge=3600, le=86400)
    daily_health_max_age_hours: float = Field(default=30.0, gt=1, le=168)
    daily_top_opportunities_limit: int = Field(default=10, ge=1, le=100)
    daily_min_decision_coverage_ratio: float = Field(default=0.95, gt=0, le=1)
    daily_max_attempts_per_day: int = Field(default=4, ge=1, le=10)
    daily_retry_initial_delay_seconds: int = Field(default=900, ge=1, le=21600)
    daily_retry_max_delay_seconds: int = Field(default=3600, ge=1, le=86400)
    daily_schedule_time: str = "02:00"
    daily_timezone: str = "Asia/Shanghai"
    daily_run_on_start: bool = False
    daily_catch_up_on_start: bool = True
    daily_alert_webhook_url: str | None = None
    daily_alert_webhook_bearer_token: SecretStr | None = None
    daily_result_webhook_url: str | None = None
    daily_result_webhook_bearer_token: SecretStr | None = None
    daily_result_webhook_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    daily_result_webhook_max_attempts: int = Field(default=3, ge=1, le=10)
    daily_advisory_status: str = "RESEARCH_ONLY"
    market_baseline_config_path: Path = Path("config/market_baseline.yaml")
    market_data_mode: str = "sample"
    market_queue: str = "market"
    valuation_config_path: Path = Path("config/valuation.yaml")
    valuation_queue: str = "valuation"
    api_key: str = Field(default="dev-only-change-me", min_length=16)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as config_file:
        value = yaml.safe_load(config_file)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration must be a mapping: {path}")
    return value

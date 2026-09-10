from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta

from home_radar_models.enums import DataMode
from home_radar_shared.config import get_settings
from home_radar_shared.database import get_session_factory

from home_radar_market.config import load_market_config
from home_radar_market.materializer import materialize_baselines
from home_radar_market.scheduler import schedule_next_materialization
from home_radar_market.windows import SHANGHAI


def materialize_market_baselines_job(as_of_date_iso: str | None = None) -> dict[str, object]:
    """RQ entry point. It schedules its successor before doing idempotent work."""
    settings = get_settings()
    schedule_next_materialization()
    as_of_date = (
        date.fromisoformat(as_of_date_iso)
        if as_of_date_iso is not None
        else datetime.now(SHANGHAI).date() - timedelta(days=1)
    )
    config = load_market_config(settings.market_baseline_config_path)
    with get_session_factory()() as session:
        result = materialize_baselines(
            session,
            as_of_date=as_of_date,
            data_mode=DataMode(settings.market_data_mode),
            config=config,
        )
    return asdict(result)

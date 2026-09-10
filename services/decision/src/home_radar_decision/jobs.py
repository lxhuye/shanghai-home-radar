from __future__ import annotations

from datetime import UTC, datetime

from home_radar_forecasting.config import load_future_config
from home_radar_market.config import load_market_config
from home_radar_models.enums import DataMode
from home_radar_shared.config import get_settings
from home_radar_shared.database import get_session_factory
from home_radar_valuation.config import load_valuation_config

from home_radar_decision.config import load_decision_config
from home_radar_decision.materializer import materialize_decisions


def materialize_decision_job(
    data_mode: str | None = None,
    as_of_iso: str | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    settings = get_settings()
    mode = DataMode(data_mode or settings.market_data_mode)
    as_of = datetime.fromisoformat(as_of_iso) if as_of_iso else datetime.now(UTC)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    with get_session_factory()() as session:
        result = materialize_decisions(
            session,
            mode,
            as_of,
            load_market_config(settings.market_baseline_config_path),
            load_valuation_config(settings.valuation_config_path),
            load_future_config(settings.forecasting_config_path),
            load_decision_config(settings.decision_config_path),
            limit=limit,
        )
    return {
        "data_mode": mode.value,
        "as_of": as_of.isoformat(),
        "evaluated": result.evaluated,
        "cache_hits": result.cache_hits,
        "failed": result.failed,
        "failures": list(result.failures),
        "skipped_insufficient_data": result.skipped_insufficient_data,
        "skips": list(result.skips),
    }

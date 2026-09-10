from __future__ import annotations

from datetime import UTC, datetime

from home_radar_market.config import load_market_config
from home_radar_models.enums import DataMode
from home_radar_shared.config import get_settings
from home_radar_shared.database import get_session_factory

from home_radar_valuation.config import load_valuation_config
from home_radar_valuation.materializer import materialize_active_listings


def materialize_valuation_job(
    data_mode: str | None = None,
    as_of_iso: str | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    settings = get_settings()
    mode = DataMode(data_mode or settings.market_data_mode)
    as_of = datetime.fromisoformat(as_of_iso) if as_of_iso else datetime.now(UTC)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    market_config = load_market_config(settings.market_baseline_config_path)
    valuation_config = load_valuation_config(settings.valuation_config_path)
    with get_session_factory()() as session:
        result = materialize_active_listings(
            session,
            mode,
            as_of,
            market_config,
            valuation_config,
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

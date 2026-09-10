from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict

from home_radar_models.enums import DataMode
from home_radar_shared.config import get_settings
from home_radar_shared.logging import configure_logging

from home_radar_collector.database import get_session_factory
from home_radar_collector.registry import build_configured_adapter
from home_radar_collector.service import CollectorService


def collect_configured_source(run_id: str | None = None) -> dict[str, object]:
    settings = get_settings()
    configure_logging(settings.log_level)
    adapter = build_configured_adapter(settings)
    with get_session_factory()() as session:
        result = asyncio.run(
            CollectorService(
                session,
                final_after_missing_runs=settings.disappearance_final_after_runs,
                data_mode=DataMode(settings.market_data_mode),
                min_complete_items=settings.collector_min_complete_items,
                min_complete_count_ratio=settings.collector_min_complete_count_ratio,
            ).collect(adapter, run_id=uuid.UUID(run_id) if run_id else None)
        )
    return asdict(result)

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from home_radar_shared.config import get_settings
from home_radar_shared.logging import configure_logging

from home_radar_api.routes import (
    collection_router,
    decision_router,
    future_router,
    health_router,
    listings_router,
    market_router,
    operations_router,
    valuation_router,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    yield


app = FastAPI(
    title="Shanghai Home Radar API",
    version="0.1.0",
    description="Private source-independent property intelligence API",
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(listings_router)
app.include_router(collection_router)
app.include_router(decision_router)
app.include_router(market_router)
app.include_router(valuation_router)
app.include_router(future_router)
app.include_router(operations_router)

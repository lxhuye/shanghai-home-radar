from home_radar_api.routes.collection import router as collection_router
from home_radar_api.routes.decision import router as decision_router
from home_radar_api.routes.future import router as future_router
from home_radar_api.routes.health import router as health_router
from home_radar_api.routes.listings import router as listings_router
from home_radar_api.routes.market import router as market_router
from home_radar_api.routes.operations import router as operations_router
from home_radar_api.routes.valuation import router as valuation_router

__all__ = [
    "collection_router",
    "decision_router",
    "future_router",
    "health_router",
    "listings_router",
    "market_router",
    "operations_router",
    "valuation_router",
]

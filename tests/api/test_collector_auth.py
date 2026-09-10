import httpx
from home_radar_api.main import app


async def test_mutating_collector_endpoint_requires_api_key() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/api/v1/collector/runs")
        incorrect = await client.post("/api/v1/collector/runs", headers={"X-API-Key": "wrong-key"})

    assert missing.status_code == 401
    assert incorrect.status_code == 401

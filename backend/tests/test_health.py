from httpx import ASGITransport, AsyncClient

from a_share_radar.main import create_app


async def test_health_returns_ok(tmp_path):
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

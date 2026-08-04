from fastapi import FastAPI

from a_share_radar.config import Settings


def create_app(settings: Settings | None = None, source: object | None = None) -> FastAPI:
    app = FastAPI(title="A 股雷达", version="0.1.0")
    app.state.settings = settings or Settings()
    app.state.source = source

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

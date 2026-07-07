from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.instruments import router as instruments_router
from app.api.portfolios import router as portfolios_router
from app.api.positions import router as positions_router
from app.api.valuation import router as valuation_router


def create_app() -> FastAPI:
    app = FastAPI(title="Investment Guru")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(instruments_router)
    app.include_router(portfolios_router)
    app.include_router(positions_router)
    app.include_router(valuation_router)
    return app


app = create_app()

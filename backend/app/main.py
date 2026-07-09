import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.guru import router as guru_router
from app.api.imports import router as imports_router
from app.api.instruments import router as instruments_router
from app.api.orso import router as orso_router
from app.api.portfolios import router as portfolios_router
from app.api.positions import router as positions_router
from app.api.signals import router as signals_router
from app.api.valuation import router as valuation_router

logger = logging.getLogger("app.main")


def _log_catch_up_result(task: asyncio.Task) -> None:
    """Done-callback for the fire-and-forget catch-up task: surface any real
    failure, but never let a shutdown-time cancellation be logged as noise."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("guru catch-up task failed", exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.guru.scheduler import catch_up, create_scheduler

    sched = create_scheduler()
    sched.start()
    task = asyncio.create_task(catch_up())
    task.add_done_callback(_log_catch_up_result)
    yield
    task.cancel()
    sched.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Investment Guru", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(guru_router)
    app.include_router(imports_router)
    app.include_router(instruments_router)
    app.include_router(orso_router)
    app.include_router(portfolios_router)
    app.include_router(positions_router)
    app.include_router(signals_router)
    app.include_router(valuation_router)
    return app


app = create_app()

from contextlib import asynccontextmanager

from fastapi import FastAPI

from photo_flow.api.jobs import JobManager
from photo_flow.api.routes_jobs import router as jobs_router
from photo_flow.api.routes_ops import router as ops_router
from photo_flow.api.routes_status import router as status_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.job_manager = JobManager()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Photo-Flow Control Panel",
        version="0.4.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(status_router)
    app.include_router(jobs_router)
    app.include_router(ops_router)

    return app


app = create_app()

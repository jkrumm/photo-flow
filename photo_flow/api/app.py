import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from photo_flow.api.jobs import JobManager, sweep_interrupted_jobs
from photo_flow.api.routes_analytics import router as analytics_router
from photo_flow.api.routes_collections import router as collections_router
from photo_flow.api.routes_config import router as config_router
from photo_flow.api.routes_jobs import router as jobs_router
from photo_flow.api.routes_ops import router as ops_router
from photo_flow.api.routes_photos import router as photos_router
from photo_flow.api.routes_status import router as status_router

logger = logging.getLogger(__name__)

# Resolve dist path relative to this file: photo_flow/api/app.py → repo root → control_panel/web/dist
_DIST_PATH = Path(__file__).parent.parent.parent / "control_panel" / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything can be enqueued: reconcile jobs the previous process was still
    # running when it died. A fresh manager owns nothing, so anything left `queued` or
    # `running` in the durable record is residue of a crash / logout / `make reload`,
    # and is marked `interrupted` rather than silently disappearing.
    app.state.interrupted_jobs = sweep_interrupted_jobs()
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
    app.include_router(analytics_router)
    # Culling view. Its routes sit under /api/photos so the SPA can own the
    # client-side /photos route without the catch-all below shadowing the API.
    app.include_router(photos_router)
    # Saved collections. Same /api prefix requirement, same catch-all hazard.
    app.include_router(collections_router)
    # The resolved library configuration, read-only. Same /api prefix, same hazard.
    app.include_router(config_router)

    # Serve the built SPA. This catch-all is registered after all API routes so those
    # match first. Any path that doesn't match an API route falls through to here:
    # known static files are served directly; everything else gets index.html (SPA routing).
    if _DIST_PATH.exists():
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            candidate = _DIST_PATH / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(_DIST_PATH / "index.html"))
    else:
        logger.info(
            "SPA dist not found at %s — run `npm run build` in control_panel/web/ to enable static serving",
            _DIST_PATH,
        )

    return app


app = create_app()

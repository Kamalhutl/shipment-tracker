"""
Shipment Tracking Platform — Web Application
Entry point for the FastAPI website layer.

This module initialises the app, mounts static files,
registers all route blueprints, and exposes a health endpoint.
It intentionally delegates ALL tracking logic to the existing
adap/tracker.py adapter — this file never touches scrapers,
main.py, or the Excel workflow.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Resolve project root so imports work when running from any CWD
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from website.routes.tracking import router as tracking_router  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown hooks)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Log startup and shutdown events."""
    logger.info("Shipment Tracker website starting up …")
    yield
    logger.info("Shipment Tracker website shut down.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(
        title="Shipment Tracker",
        description=(
            "Professional shipment tracking platform. "
            "Powered by an existing multi-carrier adapter engine."
        ),
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # CORS — allow same-origin + localhost dev ports
    # ------------------------------------------------------------------
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:3000",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Static files
    # ------------------------------------------------------------------
    static_dir = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------
    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.cache = None
    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    application.include_router(tracking_router, prefix="/api")

    # ------------------------------------------------------------------
    # Homepage — serve index.html via Jinja2 for future templating hooks
    # ------------------------------------------------------------------
    @application.get("/", include_in_schema=False)
    async def homepage(request: Request):
        return templates.TemplateResponse(request=request, name="index.html")

    # ------------------------------------------------------------------
    # Health check — used by load balancers / monitoring tools
    # ------------------------------------------------------------------
    @application.get("/health", tags=["ops"], summary="Health check")
    async def health() -> dict[str, str]:
        """Returns 200 OK when the service is running."""
        return {"status": "ok", "service": "shipment-tracker-web"}

    # ------------------------------------------------------------------
    # Global exception handler — never expose internal stack traces
    # ------------------------------------------------------------------
    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s", request.url)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "An unexpected server error occurred. Please try again.",
            },
        )

    return application


# ---------------------------------------------------------------------------
# Application instance (used by uvicorn)
# ---------------------------------------------------------------------------
app = create_app()


# ---------------------------------------------------------------------------
# Dev runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "website.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(Path(__file__).parent)],
        log_level="info",
    )

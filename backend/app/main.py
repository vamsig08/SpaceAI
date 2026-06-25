"""FastAPI application factory and lifespan management.

Creates the SpaceAI application with:
- Async lifespan for startup/shutdown (DB engine, TaskManager, migrations)
- CORS middleware
- Health check endpoint
- Structured logging initialization
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import (
    create_engine,
    create_session_factory,
    dispose_engine,
    verify_database_connection,
)
from app.core.logging import get_logger, setup_logging
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskManager

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialize and tear down shared resources."""
    settings = get_settings()

    # Initialize logging
    setup_logging(settings)
    logger.info("application_starting", env=settings.env.value, version=settings.version)

    # Initialize database
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await verify_database_connection(engine)

    # Initialize task manager and progress reporter
    task_manager = TaskManager(thread_pool_size=settings.scanner_thread_pool_size)
    progress_reporter = ProgressReporter()

    # Store in app state for dependency injection
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.task_manager = task_manager
    app.state.progress_reporter = progress_reporter

    logger.info("application_started")

    yield

    # Shutdown
    logger.info("application_shutting_down")
    await task_manager.shutdown(timeout=30.0)
    await dispose_engine(engine)
    logger.info("application_shutdown_complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="SpaceAI",
        description="AI-powered storage optimization platform",
        version=settings.version,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    app.include_router(api_router)

    # Health check
    @app.get("/api/v1/health")
    async def health_check() -> dict:
        return {
            "status": "healthy",
            "version": settings.version,
            "environment": settings.env.value,
        }

    return app


# Application instance used by uvicorn
app = create_app()

"""FastAPI dependency injection providers.

Centralizes all dependency factories for clean injection into route handlers.
Each dependency pulls from app.state (set during lifespan) and yields
properly-scoped instances.
"""

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.analytics_service import AnalyticsService
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskManager


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Extract session factory from app state."""
    return request.app.state.session_factory


async def get_session(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session with automatic commit/rollback."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_task_manager(request: Request) -> TaskManager:
    """Extract task manager from app state."""
    return request.app.state.task_manager


def get_progress_reporter(request: Request) -> ProgressReporter:
    """Extract progress reporter from app state."""
    return request.app.state.progress_reporter


async def get_analytics_service(
    session: AsyncSession = Depends(get_session),
) -> AnalyticsService:
    """Create an AnalyticsService scoped to the request session."""
    return AnalyticsService(session)

"""In-process async background task manager.

Provides lifecycle management for long-running operations (scans, hashing,
analytics) without external dependencies like Redis or Celery.

Architecture:
- TaskManager is a singleton created at app startup, destroyed on shutdown.
- Tasks run as asyncio.Tasks with thread pool dispatch for blocking I/O.
- Concurrency is controlled via per-type semaphores.
- Cancellation uses cooperative asyncio.Event signaling.
- Progress is reported via ProgressReporter (fan-out to SSE subscribers).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.logging import bind_correlation_id, get_logger

logger = get_logger(__name__)


class TaskType(str, Enum):
    """Types of background tasks with associated concurrency limits."""

    SCAN = "scan"
    HASH = "hash"
    ANALYTICS = "analytics"
    RECOMMENDATION = "recommendation"
    CLEANUP = "cleanup"


class TaskStatus(str, Enum):
    """Lifecycle states for a background task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskProgress:
    """Mutable progress state updated by the running task."""

    files_scanned: int = 0
    dirs_scanned: int = 0
    total_bytes_scanned: int = 0
    current_directory: str = ""
    errors_skipped: int = 0
    checkpoint_count: int = 0


@dataclass
class TaskState:
    """Complete state of a managed background task."""

    task_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    progress: TaskProgress = field(default_factory=TaskProgress)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Check if task has reached a terminal state."""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )

    @property
    def duration_seconds(self) -> float | None:
        """Compute task duration in seconds, or None if not started/finished."""
        if self.started_at is None:
            return None
        end = self.completed_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()


# Type alias for task functions: async callables that receive TaskState and kwargs
TaskFunction = Callable[..., Coroutine[Any, Any, None]]


class TaskManager:
    """Manages background task lifecycle, concurrency, and cancellation.

    Usage:
        manager = TaskManager(thread_pool_size=4)
        task_id = await manager.submit(TaskType.SCAN, scan_function, root_path="/home")
        state = manager.get_status(task_id)
        await manager.cancel(task_id)
        await manager.shutdown()
    """

    def __init__(self, thread_pool_size: int = 4) -> None:
        self._registry: dict[str, TaskState] = {}
        self._async_tasks: dict[str, asyncio.Task[None]] = {}
        self._thread_pool = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="spaceai-worker",
        )

        # Per-type concurrency semaphores
        self._semaphores: dict[TaskType, asyncio.Semaphore] = {
            TaskType.SCAN: asyncio.Semaphore(1),
            TaskType.HASH: asyncio.Semaphore(1),
            TaskType.ANALYTICS: asyncio.Semaphore(2),
            TaskType.RECOMMENDATION: asyncio.Semaphore(2),
            TaskType.CLEANUP: asyncio.Semaphore(1),
        }

        self._shutting_down = False
        logger.info("task_manager_initialized", thread_pool_size=thread_pool_size)

    @property
    def thread_pool(self) -> ThreadPoolExecutor:
        """Expose thread pool for scanner workers to dispatch blocking I/O."""
        return self._thread_pool

    async def submit(
        self,
        task_type: TaskType,
        task_fn: TaskFunction,
        **kwargs: Any,
    ) -> str:
        """Submit a task for background execution.

        The task function receives a TaskState as its first argument plus any
        additional kwargs. The task must cooperatively check
        task_state.cancel_event.is_set() periodically.

        Args:
            task_type: Type of task (controls concurrency limits).
            task_fn: Async function to execute.
            **kwargs: Additional arguments passed to task_fn.

        Returns:
            The generated task_id string.

        Raises:
            RuntimeError: If the manager is shutting down.
        """
        if self._shutting_down:
            raise RuntimeError("TaskManager is shutting down, cannot accept new tasks")

        task_id = str(uuid.uuid4())
        state = TaskState(task_id=task_id, task_type=task_type)
        state.metadata = kwargs.copy()
        self._registry[task_id] = state

        async_task = asyncio.create_task(
            self._execute(state, task_fn, **kwargs),
            name=f"spaceai-{task_type.value}-{task_id[:8]}",
        )
        self._async_tasks[task_id] = async_task

        logger.info(
            "task_submitted",
            task_id=task_id,
            task_type=task_type.value,
        )
        return task_id

    async def cancel(self, task_id: str) -> bool:
        """Signal a task to cancel.

        Sets the cancel_event which the task function should check periodically.
        Does NOT force-kill the task — cancellation is cooperative.

        Args:
            task_id: ID of the task to cancel.

        Returns:
            True if the task was found and signaled, False if not found or already terminal.
        """
        state = self._registry.get(task_id)
        if state is None:
            return False

        if state.is_terminal:
            return False

        state.cancel_event.set()
        logger.info("task_cancel_requested", task_id=task_id)
        return True

    def get_status(self, task_id: str) -> TaskState | None:
        """Get the current state of a task.

        Args:
            task_id: ID of the task to query.

        Returns:
            TaskState or None if not found.
        """
        return self._registry.get(task_id)

    def list_tasks(
        self,
        task_type: TaskType | None = None,
        include_terminal: bool = False,
    ) -> list[TaskState]:
        """List all tracked tasks with optional filtering.

        Args:
            task_type: Filter by type, or None for all types.
            include_terminal: Include completed/failed/cancelled tasks.

        Returns:
            List of matching TaskState objects.
        """
        results = []
        for state in self._registry.values():
            if task_type is not None and state.task_type != task_type:
                continue
            if not include_terminal and state.is_terminal:
                continue
            results.append(state)
        return results

    def has_running_task(self, task_type: TaskType) -> bool:
        """Check if any task of the given type is currently running.

        Args:
            task_type: The type to check.

        Returns:
            True if a non-terminal task of this type exists.
        """
        return any(
            s.task_type == task_type and not s.is_terminal
            for s in self._registry.values()
        )

    async def shutdown(self, timeout: float = 30.0) -> None:
        """Gracefully shut down: cancel all running tasks and wait.

        Args:
            timeout: Maximum seconds to wait for tasks to finish.
        """
        self._shutting_down = True
        logger.info("task_manager_shutting_down", active_tasks=len(self._async_tasks))

        # Signal all running tasks to cancel
        for state in self._registry.values():
            if not state.is_terminal:
                state.cancel_event.set()

        # Wait for all async tasks to finish
        if self._async_tasks:
            pending = [t for t in self._async_tasks.values() if not t.done()]
            if pending:
                await asyncio.wait(pending, timeout=timeout)

        # Shutdown thread pool
        self._thread_pool.shutdown(wait=False, cancel_futures=True)
        logger.info("task_manager_shutdown_complete")

    async def _execute(
        self,
        state: TaskState,
        task_fn: TaskFunction,
        **kwargs: Any,
    ) -> None:
        """Internal wrapper that handles semaphore acquisition, lifecycle, and errors."""
        semaphore = self._semaphores[state.task_type]
        correlation_id = f"task-{state.task_id}"

        try:
            # Wait for semaphore (respects concurrency limit)
            await semaphore.acquire()
            bind_correlation_id(correlation_id)

            state.status = TaskStatus.RUNNING
            state.started_at = datetime.now(timezone.utc)
            logger.info(
                "task_started",
                task_id=state.task_id,
                task_type=state.task_type.value,
            )

            # Execute the actual task function
            await task_fn(state, **kwargs)

            # If task completed without setting status (e.g. cancellation handled inside)
            if state.status == TaskStatus.RUNNING:
                state.status = TaskStatus.COMPLETED

        except asyncio.CancelledError:
            state.status = TaskStatus.CANCELLED
            logger.info("task_cancelled", task_id=state.task_id)

        except Exception as e:
            state.status = TaskStatus.FAILED
            state.error = f"{type(e).__name__}: {e}"
            logger.error(
                "task_failed",
                task_id=state.task_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

        finally:
            state.completed_at = datetime.now(timezone.utc)
            semaphore.release()

            duration = state.duration_seconds
            logger.info(
                "task_finished",
                task_id=state.task_id,
                status=state.status.value,
                duration_seconds=duration,
            )

            # Clean up async task reference
            self._async_tasks.pop(state.task_id, None)

"""Progress reporting system for SSE fan-out to subscribers.

ProgressReporter bridges background tasks to SSE endpoints by maintaining
per-task subscriber queues. Events are buffered for reconnection support.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ProgressEvent:
    """A single progress event to be sent via SSE."""

    event_type: str  # progress | checkpoint | error | completed | failed | cancelled
    data: dict[str, Any]
    event_id: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_sse(self) -> str:
        """Format this event as an SSE wire-protocol string."""
        lines = [
            f"id: {self.event_id}",
            f"event: {self.event_type}",
            f"data: {json.dumps(self.data)}",
            "",
            "",
        ]
        return "\n".join(lines)

    @property
    def is_terminal(self) -> bool:
        """Check if this event signals end of stream."""
        return self.event_type in ("completed", "failed", "cancelled")


class ProgressReporter:
    """Fan-out progress events to SSE subscribers with bounded replay buffer.

    Thread-safe via asyncio.Lock. Supports:
    - Multiple concurrent subscribers per task
    - Buffered replay on reconnection via last_event_id
    - Automatic dead subscriber cleanup on queue overflow
    """

    def __init__(self, buffer_size: int = 100) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[ProgressEvent]]] = defaultdict(
            list
        )
        self._buffers: dict[str, deque[ProgressEvent]] = defaultdict(
            lambda: deque(maxlen=buffer_size)
        )
        self._lock = asyncio.Lock()
        self._buffer_size = buffer_size

    async def subscribe(
        self, task_id: str, last_event_id: int | None = None
    ) -> asyncio.Queue[ProgressEvent]:
        """Create a new subscriber queue for a task.

        If last_event_id is provided, replays buffered events that occurred
        after that ID (supports SSE reconnection via Last-Event-ID header).

        Args:
            task_id: The task to subscribe to.
            last_event_id: Replay events after this ID (millisecond timestamp).

        Returns:
            An asyncio.Queue that will receive ProgressEvent objects.
        """
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue(maxsize=50)

        async with self._lock:
            # Replay missed events on reconnection
            if last_event_id is not None:
                for event in self._buffers[task_id]:
                    if event.event_id > last_event_id:
                        try:
                            queue.put_nowait(event)
                        except asyncio.QueueFull:
                            break

            self._subscribers[task_id].append(queue)

        logger.debug(
            "progress_subscriber_added",
            task_id=task_id,
            subscriber_count=len(self._subscribers[task_id]),
        )
        return queue

    async def unsubscribe(
        self, task_id: str, queue: asyncio.Queue[ProgressEvent]
    ) -> None:
        """Remove a subscriber queue.

        Args:
            task_id: The task the queue is subscribed to.
            queue: The queue to remove.
        """
        async with self._lock:
            subscribers = self._subscribers.get(task_id, [])
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers and task_id in self._subscribers:
                del self._subscribers[task_id]

    async def emit(self, task_id: str, event: ProgressEvent) -> None:
        """Push an event to all subscribers of a task and buffer it.

        Slow consumers (full queues) are automatically dropped.

        Args:
            task_id: The task emitting the event.
            event: The progress event to distribute.
        """
        async with self._lock:
            self._buffers[task_id].append(event)

            dead_queues: list[asyncio.Queue[ProgressEvent]] = []
            for queue in self._subscribers.get(task_id, []):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    dead_queues.append(queue)

            # Remove slow consumers
            for dead in dead_queues:
                self._subscribers[task_id].remove(dead)
                logger.debug(
                    "progress_slow_subscriber_dropped",
                    task_id=task_id,
                )

    async def emit_progress(
        self,
        task_id: str,
        files_scanned: int,
        dirs_scanned: int,
        current_directory: str,
        total_bytes_scanned: int,
        files_per_second: float,
        errors_skipped: int = 0,
        eta_seconds: float | None = None,
    ) -> None:
        """Convenience method to emit a standard progress event.

        Args:
            task_id: The scan task ID.
            files_scanned: Total files processed so far.
            dirs_scanned: Total directories entered so far.
            current_directory: Directory currently being processed.
            total_bytes_scanned: Cumulative bytes of all scanned files.
            files_per_second: Current processing rate.
            errors_skipped: Number of files skipped due to errors.
            eta_seconds: Estimated time remaining in seconds.
        """
        event = ProgressEvent(
            event_type="progress",
            data={
                "files_scanned": files_scanned,
                "dirs_scanned": dirs_scanned,
                "current_directory": current_directory,
                "total_bytes_scanned": total_bytes_scanned,
                "files_per_second": round(files_per_second, 1),
                "errors_skipped": errors_skipped,
                "eta_seconds": round(eta_seconds, 1) if eta_seconds else None,
            },
        )
        await self.emit(task_id, event)

    async def emit_completed(
        self,
        task_id: str,
        scan_id: str,
        total_files: int,
        total_dirs: int,
        total_bytes: int,
        duration_seconds: float,
        files_per_second: float,
        errors_skipped: int = 0,
    ) -> None:
        """Emit a terminal completion event.

        Args:
            task_id: The scan task ID.
            scan_id: The database scan record ID.
            total_files: Final file count.
            total_dirs: Final directory count.
            total_bytes: Total bytes discovered.
            duration_seconds: Total scan duration.
            files_per_second: Average processing rate.
            errors_skipped: Total files skipped due to errors.
        """
        event = ProgressEvent(
            event_type="completed",
            data={
                "scan_id": scan_id,
                "total_files": total_files,
                "total_dirs": total_dirs,
                "total_bytes": total_bytes,
                "duration_seconds": round(duration_seconds, 2),
                "files_per_second": round(files_per_second, 1),
                "errors_skipped": errors_skipped,
            },
        )
        await self.emit(task_id, event)

    async def emit_failed(
        self, task_id: str, error_type: str, message: str, files_scanned: int = 0
    ) -> None:
        """Emit a terminal failure event.

        Args:
            task_id: The task that failed.
            error_type: Exception class name.
            message: Error description.
            files_scanned: Files processed before failure.
        """
        event = ProgressEvent(
            event_type="failed",
            data={
                "error_type": error_type,
                "message": message,
                "files_scanned_before_failure": files_scanned,
            },
        )
        await self.emit(task_id, event)

    def get_subscriber_count(self, task_id: str) -> int:
        """Get the number of active subscribers for a task."""
        return len(self._subscribers.get(task_id, []))

    async def cleanup(self, task_id: str) -> None:
        """Remove all subscribers and buffer for a completed task.

        Call this after a terminal event has been emitted and all
        subscribers have had time to receive it.

        Args:
            task_id: The task to clean up.
        """
        async with self._lock:
            self._subscribers.pop(task_id, None)
            self._buffers.pop(task_id, None)

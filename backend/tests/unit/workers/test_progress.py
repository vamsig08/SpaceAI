"""Unit tests for ProgressReporter SSE fan-out system."""

import asyncio

import pytest

from app.workers.progress import ProgressEvent, ProgressReporter


class TestProgressEvent:
    """Tests for ProgressEvent data class."""

    def test_to_sse_format(self) -> None:
        evt = ProgressEvent(
            event_type="progress",
            data={"files_scanned": 100},
            event_id=1234567890,
        )
        sse = evt.to_sse()
        assert "id: 1234567890" in sse
        assert "event: progress" in sse
        assert '"files_scanned": 100' in sse
        assert sse.endswith("\n\n")

    def test_is_terminal_for_completed(self) -> None:
        assert ProgressEvent("completed", {}).is_terminal is True
        assert ProgressEvent("failed", {}).is_terminal is True
        assert ProgressEvent("cancelled", {}).is_terminal is True

    def test_is_not_terminal_for_progress(self) -> None:
        assert ProgressEvent("progress", {}).is_terminal is False
        assert ProgressEvent("checkpoint", {}).is_terminal is False

    def test_event_id_auto_generated(self) -> None:
        evt = ProgressEvent("progress", {})
        assert evt.event_id > 0


class TestProgressReporterSubscription:
    """Tests for subscribe/unsubscribe."""

    async def test_subscribe_returns_queue(self) -> None:
        reporter = ProgressReporter()
        queue = await reporter.subscribe("task-1")
        assert isinstance(queue, asyncio.Queue)

    async def test_subscriber_count(self) -> None:
        reporter = ProgressReporter()
        await reporter.subscribe("task-1")
        await reporter.subscribe("task-1")
        assert reporter.get_subscriber_count("task-1") == 2
        assert reporter.get_subscriber_count("task-2") == 0

    async def test_unsubscribe_removes_queue(self) -> None:
        reporter = ProgressReporter()
        queue = await reporter.subscribe("task-1")
        await reporter.unsubscribe("task-1", queue)
        assert reporter.get_subscriber_count("task-1") == 0


class TestProgressReporterEmit:
    """Tests for event emission and fan-out."""

    async def test_emit_delivers_to_subscriber(self) -> None:
        reporter = ProgressReporter()
        queue = await reporter.subscribe("task-1")

        event = ProgressEvent("progress", {"count": 42})
        await reporter.emit("task-1", event)

        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.event_type == "progress"
        assert received.data["count"] == 42

    async def test_emit_fans_out_to_multiple_subscribers(self) -> None:
        reporter = ProgressReporter()
        q1 = await reporter.subscribe("task-1")
        q2 = await reporter.subscribe("task-1")

        await reporter.emit("task-1", ProgressEvent("progress", {"x": 1}))

        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert r1.data["x"] == 1
        assert r2.data["x"] == 1

    async def test_emit_does_not_cross_tasks(self) -> None:
        reporter = ProgressReporter()
        q1 = await reporter.subscribe("task-1")
        q2 = await reporter.subscribe("task-2")

        await reporter.emit("task-1", ProgressEvent("progress", {}))

        assert not q2.empty() is False  # q2 should be empty
        assert q1.qsize() == 1
        assert q2.qsize() == 0


class TestProgressReporterReplay:
    """Tests for replay buffer on reconnection."""

    async def test_replay_buffered_events(self) -> None:
        reporter = ProgressReporter(buffer_size=50)

        # Emit events before subscriber connects
        await reporter.emit("task-1", ProgressEvent("progress", {"n": 1}, event_id=100))
        await reporter.emit("task-1", ProgressEvent("progress", {"n": 2}, event_id=200))
        await reporter.emit("task-1", ProgressEvent("progress", {"n": 3}, event_id=300))

        # Subscribe with last_event_id=150 — should replay events 200 and 300
        queue = await reporter.subscribe("task-1", last_event_id=150)
        assert queue.qsize() == 2

        e1 = await queue.get()
        assert e1.event_id == 200
        e2 = await queue.get()
        assert e2.event_id == 300

    async def test_no_replay_without_last_event_id(self) -> None:
        reporter = ProgressReporter(buffer_size=50)
        await reporter.emit("task-1", ProgressEvent("progress", {}, event_id=100))

        queue = await reporter.subscribe("task-1", last_event_id=None)
        assert queue.qsize() == 0


class TestProgressReporterConvenience:
    """Tests for convenience emit methods."""

    async def test_emit_progress_creates_event(self) -> None:
        reporter = ProgressReporter()
        queue = await reporter.subscribe("t1")

        await reporter.emit_progress(
            task_id="t1",
            files_scanned=5000,
            dirs_scanned=100,
            current_directory="/tmp/scan",
            total_bytes_scanned=1000000,
            files_per_second=500.5,
        )

        evt = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert evt.event_type == "progress"
        assert evt.data["files_scanned"] == 5000
        assert evt.data["files_per_second"] == 500.5

    async def test_emit_completed_creates_terminal_event(self) -> None:
        reporter = ProgressReporter()
        queue = await reporter.subscribe("t1")

        await reporter.emit_completed(
            task_id="t1",
            scan_id="scan-uuid",
            total_files=100000,
            total_dirs=5000,
            total_bytes=50000000000,
            duration_seconds=60.5,
            files_per_second=1652.9,
        )

        evt = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert evt.event_type == "completed"
        assert evt.is_terminal is True
        assert evt.data["total_files"] == 100000

    async def test_emit_failed_creates_terminal_event(self) -> None:
        reporter = ProgressReporter()
        queue = await reporter.subscribe("t1")

        await reporter.emit_failed("t1", "OSError", "disk full", files_scanned=9999)

        evt = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert evt.event_type == "failed"
        assert evt.data["error_type"] == "OSError"


class TestProgressReporterCleanup:
    """Tests for task cleanup."""

    async def test_cleanup_removes_task_data(self) -> None:
        reporter = ProgressReporter()
        await reporter.subscribe("task-1")
        await reporter.emit("task-1", ProgressEvent("progress", {}))

        await reporter.cleanup("task-1")
        assert reporter.get_subscriber_count("task-1") == 0

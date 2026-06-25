"""Unit tests for TaskManager lifecycle and concurrency."""

import asyncio

import pytest

from app.workers.task_manager import (
    TaskManager,
    TaskState,
    TaskStatus,
    TaskType,
)


class TestTaskSubmission:
    """Tests for task submission."""

    async def test_submit_returns_task_id(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            async def noop(state: TaskState) -> None:
                pass

            task_id = await manager.submit(TaskType.ANALYTICS, noop)
            assert task_id is not None
            assert len(task_id) == 36  # UUID format
        finally:
            await manager.shutdown(timeout=5)

    async def test_task_transitions_to_running(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            started = asyncio.Event()

            async def slow_task(state: TaskState) -> None:
                started.set()
                await asyncio.sleep(0.5)

            task_id = await manager.submit(TaskType.ANALYTICS, slow_task)
            await started.wait()

            state = manager.get_status(task_id)
            assert state is not None
            assert state.status == TaskStatus.RUNNING
        finally:
            await manager.shutdown(timeout=5)

    async def test_task_completes_successfully(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            async def quick_task(state: TaskState) -> None:
                await asyncio.sleep(0.01)

            task_id = await manager.submit(TaskType.ANALYTICS, quick_task)
            await asyncio.sleep(0.1)

            state = manager.get_status(task_id)
            assert state is not None
            assert state.status == TaskStatus.COMPLETED
            assert state.duration_seconds is not None
            assert state.duration_seconds > 0
        finally:
            await manager.shutdown(timeout=5)

    async def test_task_failure_captured(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            async def failing_task(state: TaskState) -> None:
                raise ValueError("test error")

            task_id = await manager.submit(TaskType.ANALYTICS, failing_task)
            await asyncio.sleep(0.1)

            state = manager.get_status(task_id)
            assert state is not None
            assert state.status == TaskStatus.FAILED
            assert "test error" in (state.error or "")
        finally:
            await manager.shutdown(timeout=5)


class TestTaskCancellation:
    """Tests for cooperative cancellation."""

    async def test_cancel_signals_event(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            started = asyncio.Event()

            async def cancellable_task(state: TaskState) -> None:
                started.set()
                while not state.cancel_event.is_set():
                    await asyncio.sleep(0.01)
                state.status = TaskStatus.CANCELLED

            task_id = await manager.submit(TaskType.SCAN, cancellable_task)
            await started.wait()

            result = await manager.cancel(task_id)
            assert result is True

            await asyncio.sleep(0.1)
            state = manager.get_status(task_id)
            assert state is not None
            assert state.status == TaskStatus.CANCELLED
        finally:
            await manager.shutdown(timeout=5)

    async def test_cancel_nonexistent_task_returns_false(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            result = await manager.cancel("nonexistent-id")
            assert result is False
        finally:
            await manager.shutdown(timeout=5)


class TestTaskConcurrency:
    """Tests for concurrency limiting."""

    async def test_scan_semaphore_limits_to_one(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            running_count = 0
            max_concurrent = 0

            async def tracked_task(state: TaskState) -> None:
                nonlocal running_count, max_concurrent
                running_count += 1
                max_concurrent = max(max_concurrent, running_count)
                await asyncio.sleep(0.05)
                running_count -= 1

            # Submit 3 scan tasks — only 1 should run at a time
            ids = []
            for _ in range(3):
                tid = await manager.submit(TaskType.SCAN, tracked_task)
                ids.append(tid)

            await asyncio.sleep(0.3)
            assert max_concurrent == 1
        finally:
            await manager.shutdown(timeout=5)

    async def test_has_running_task_returns_true(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            started = asyncio.Event()

            async def blocking(state: TaskState) -> None:
                started.set()
                await asyncio.sleep(1)

            await manager.submit(TaskType.SCAN, blocking)
            await started.wait()

            assert manager.has_running_task(TaskType.SCAN) is True
            assert manager.has_running_task(TaskType.HASH) is False
        finally:
            await manager.shutdown(timeout=5)


class TestTaskListing:
    """Tests for list_tasks."""

    async def test_list_active_tasks(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            started = asyncio.Event()

            async def blocking(state: TaskState) -> None:
                started.set()
                await asyncio.sleep(1)

            await manager.submit(TaskType.SCAN, blocking)
            await started.wait()

            tasks = manager.list_tasks()
            assert len(tasks) == 1
            assert tasks[0].task_type == TaskType.SCAN
        finally:
            await manager.shutdown(timeout=5)

    async def test_list_excludes_terminal_by_default(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        try:
            async def quick(state: TaskState) -> None:
                pass

            await manager.submit(TaskType.ANALYTICS, quick)
            await asyncio.sleep(0.1)

            active = manager.list_tasks(include_terminal=False)
            all_tasks = manager.list_tasks(include_terminal=True)
            assert len(active) == 0
            assert len(all_tasks) == 1
        finally:
            await manager.shutdown(timeout=5)


class TestTaskShutdown:
    """Tests for graceful shutdown."""

    async def test_shutdown_cancels_running_tasks(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        started = asyncio.Event()

        async def long_task(state: TaskState) -> None:
            started.set()
            while not state.cancel_event.is_set():
                await asyncio.sleep(0.01)
            state.status = TaskStatus.CANCELLED

        task_id = await manager.submit(TaskType.SCAN, long_task)
        await started.wait()

        await manager.shutdown(timeout=5)

        state = manager.get_status(task_id)
        assert state is not None
        assert state.status == TaskStatus.CANCELLED

    async def test_rejects_tasks_after_shutdown(self) -> None:
        manager = TaskManager(thread_pool_size=2)
        await manager.shutdown(timeout=1)

        async def noop(state: TaskState) -> None:
            pass

        with pytest.raises(RuntimeError, match="shutting down"):
            await manager.submit(TaskType.ANALYTICS, noop)

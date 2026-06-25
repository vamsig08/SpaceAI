# Background Task Architecture

**Reference:** ADR-003 (In-Process Async Task Manager)  
**Date:** 2026-06-23

---

## 1. Overview

SpaceAI uses an in-process task system built on Python's `asyncio` with a `ThreadPoolExecutor` for CPU/IO-bound filesystem operations. This avoids external dependencies (Redis, RabbitMQ) while supporting long-running operations that cannot complete within an HTTP request cycle.

---

## 2. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        FastAPI Application                         │
│                                                                    │
│  ┌─────────────┐     ┌──────────────────────────────────────┐   │
│  │  API Route  │     │           TaskManager                  │   │
│  │ POST /scans │────▶│                                        │   │
│  └─────────────┘     │  ┌─────────────────────────────────┐  │   │
│                       │  │        Task Registry             │  │   │
│                       │  │  task_id → TaskState             │  │   │
│                       │  │  {status, progress, errors,      │  │   │
│                       │  │   started_at, cancel_event}      │  │   │
│                       │  └─────────────────────────────────┘  │   │
│                       │                                        │   │
│                       │  ┌─────────────────────────────────┐  │   │
│                       │  │      Concurrency Limiter         │  │   │
│                       │  │  scan_semaphore: max=1            │  │   │
│                       │  │  hash_semaphore: max=1            │  │   │
│                       │  │  general_semaphore: max=3         │  │   │
│                       │  └─────────────────────────────────┘  │   │
│                       └───────────────┬──────────────────────┘   │
│                                       │                           │
│                          ┌────────────▼────────────┐             │
│                          │   ThreadPoolExecutor     │             │
│                          │   max_workers=4          │             │
│                          │                          │             │
│                          │  ┌────┐ ┌────┐ ┌────┐  │             │
│                          │  │ T1 │ │ T2 │ │ T3 │  │             │
│                          │  └────┘ └────┘ └────┘  │             │
│                          └─────────────────────────┘             │
│                                                                    │
│  ┌─────────────┐     ┌──────────────────────────────────────┐   │
│  │  SSE Route  │◀────│       ProgressReporter                │   │
│  │ GET /progress│     │  (asyncio.Queue per subscriber)       │   │
│  └─────────────┘     └──────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Specifications

### 3.1 TaskManager

The central coordinator. Singleton, created at application startup, shut down on application exit.

```python
class TaskState:
    task_id: str                    # UUIDv4
    task_type: TaskType             # scan | hash | analytics | recommendation | cleanup
    status: TaskStatus             # pending | running | completed | failed | cancelled
    progress: TaskProgress         # Mutable progress data
    cancel_event: asyncio.Event    # Signal cancellation
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None

class TaskManager:
    """Manages background task lifecycle."""
    
    async def submit(self, task_type: TaskType, task_fn: Callable, **kwargs) -> str:
        """Submit a task for background execution. Returns task_id."""
    
    async def cancel(self, task_id: str) -> bool:
        """Signal cancellation. Task must check cancel_event periodically."""
    
    def get_status(self, task_id: str) -> TaskState | None:
        """Get current state of a task."""
    
    def list_tasks(self, task_type: TaskType | None = None) -> list[TaskState]:
        """List all tasks, optionally filtered by type."""
    
    async def shutdown(self, timeout: float = 30.0) -> None:
        """Graceful shutdown: cancel running tasks, wait for completion."""
```

Lifecycle:
- Created in FastAPI `lifespan` context manager.
- Injected into services via FastAPI `Depends()`.
- Shutdown triggered by application stop signal (SIGTERM/SIGINT).

### 3.2 Concurrency Rules

| Task Type | Max Concurrent | Rationale |
|-----------|---------------|-----------|
| scan | 1 | Single scan at a time to avoid SQLite write contention |
| hash | 1 | CPU-intensive, would compete with scan for I/O |
| analytics | 2 | Read-only queries, can run alongside writes (WAL mode) |
| recommendation | 2 | Network-bound (AI API calls), no DB contention |
| cleanup | 1 | Filesystem modifications must be serialized for safety |

Implemented via `asyncio.Semaphore` per task type.

### 3.3 ThreadPoolExecutor Usage

The GIL prevents true parallelism for Python code, but filesystem syscalls (`os.scandir`, `os.stat`, `open()`, `hashlib.update()`) release the GIL. The thread pool enables parallel I/O:

```python
# In scanner worker:
loop = asyncio.get_event_loop()
file_infos = await loop.run_in_executor(
    thread_pool,
    scan_directory_batch,  # This function does os.scandir + os.stat
    directory_paths,
)
```

Thread pool configuration:
- `max_workers=4` (default). Configurable via `SPACEAI_THREAD_POOL_SIZE`.
- Threads are reused across tasks (pool persists for application lifetime).
- Each thread operates on a discrete directory (no shared mutable state).

### 3.4 Cancellation Protocol

Tasks must cooperate with cancellation:

```python
async def scan_task(task_state: TaskState, root_path: str, ...):
    for directory in walk_directories(root_path):
        # Check cancellation every directory
        if task_state.cancel_event.is_set():
            task_state.status = TaskStatus.CANCELLED
            await save_checkpoint(task_state)
            return
        
        # Process directory...
        await process_directory(directory, task_state)
```

Cancellation guarantees:
- Task will stop within 1 directory traversal (typically < 1 second).
- Partial results are committed to DB (batches already flushed are persistent).
- Checkpoint is saved so scan can be resumed later.

### 3.5 Error Handling

```python
async def _execute_task(self, task_state: TaskState, task_fn: Callable, **kwargs):
    try:
        task_state.status = TaskStatus.RUNNING
        task_state.started_at = datetime.utcnow()
        await task_fn(task_state, **kwargs)
        task_state.status = TaskStatus.COMPLETED
    except asyncio.CancelledError:
        task_state.status = TaskStatus.CANCELLED
    except Exception as e:
        task_state.status = TaskStatus.FAILED
        task_state.error = str(e)
        logger.error("task_failed", task_id=task_state.task_id, error=str(e))
    finally:
        task_state.completed_at = datetime.utcnow()
```

Error categories:
- **Recoverable** (PermissionError on single file): Log warning, skip file, continue.
- **Degraded** (AI provider timeout): Circuit breaker opens, return partial results.
- **Fatal** (disk full, DB locked permanently): Mark task as failed, log critical.

### 3.6 ProgressReporter

Bridges background tasks to SSE streams:

```python
class ProgressReporter:
    """Fan-out progress events to SSE subscribers."""
    
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}  # task_id → queues
    
    def subscribe(self, task_id: str) -> asyncio.Queue:
        """Create a new subscriber queue for a task."""
    
    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber."""
    
    async def emit(self, task_id: str, event: ProgressEvent) -> None:
        """Push event to all subscribers of this task."""
```

Events are emitted:
- Every 1,000 files scanned (batched for efficiency).
- Every 1 second (time-based fallback for slow directories).
- On checkpoint save.
- On completion/failure/cancellation.

---

## 4. Task Types

### 4.1 Filesystem Scan Task

```
Input:  root_path, scan_type (full/incremental), exclusion_patterns
Output: scan_id in DB with all files and folders populated

Steps:
1. Create scan record (status=running)
2. Load exclusion rules
3. Walk directories (generator, never materializes full list)
4. For each directory batch (1000 files):
   a. Collect FileInfo via os.scandir + os.stat (thread pool)
   b. Categorize by extension
   c. Batch insert into files table
   d. Update folder aggregates
   e. Emit progress event
   f. Check cancel_event
5. Every 10,000 files: save checkpoint
6. On completion: compute folder sizes, create storage snapshot, mark scan complete
```

### 4.2 Hash Task (Duplicate Detection)

```
Input:  scan_id
Output: duplicate_groups populated, files.sha256_hash filled

Steps:
1. Query files grouped by size (only sizes with 2+ files are candidates)
2. For each candidate group:
   a. Stream-hash files (64KB buffer) in thread pool
   b. Update files.sha256_hash
   c. Group by hash
   d. Create duplicate_group + members for groups with 2+ files
   e. Emit progress
   f. Check cancel_event
3. Update files.is_duplicate flags
```

### 4.3 Recommendation Generation Task

```
Input:  scan_id, provider, categories
Output: recommendations populated

Steps:
1. Gather analysis context (storage snapshot, top issues, workspace summary)
2. Construct prompt with structured context
3. Call AI provider (with circuit breaker)
4. Parse structured response
5. Validate and score recommendations
6. Insert into recommendations table
7. Emit completion
```

### 4.4 Cleanup Execution Task

```
Input:  cleanup_action_id (must be status=approved)
Output: files moved to trash, audit logs created

Steps:
1. Load cleanup action (verify status=approved)
2. Set status=executing
3. Create trash directory (date-stamped)
4. For each target path:
   a. Verify file still exists
   b. Move to trash location
   c. Record in manifest
   d. Create audit_log entry
   e. Emit progress
   f. Check cancel_event (rollback completed files on cancel)
5. Update cleanup_action with bytes_recovered
6. Set status=completed
```

---

## 5. Crash Recovery

### 5.1 On Application Restart

```python
async def recover_interrupted_tasks(self):
    """Called during app startup."""
    
    # Find scans that were running when the process died
    interrupted_scans = await scan_repo.find_by_status("running")
    
    for scan in interrupted_scans:
        # Mark as failed with checkpoint info preserved
        scan.status = "failed"
        scan.error_message = "Process interrupted. Resume available."
        await scan_repo.update(scan)
        
        logger.info("interrupted_scan_found", 
                    scan_id=scan.id, 
                    checkpoint=scan.checkpoint_data)
```

### 5.2 Resume Scan

When a user requests a scan on the same root_path and an interrupted scan exists:
1. Offer to resume (via API response with `resumable_scan_id`).
2. On resume: load checkpoint, skip already-scanned directories, continue from last checkpoint.
3. The files already in DB from the interrupted scan remain valid.

---

## 6. Memory Budget (500MB NFR)

| Component | Peak Memory | Notes |
|-----------|-------------|-------|
| FastAPI + Uvicorn | ~80 MB | Baseline process |
| SQLite page cache | 64 MB | Configured via PRAGMA |
| SQLite mmap | 256 MB | Virtual memory (not RSS) |
| Thread pool (4 threads) | ~4 MB | Stack per thread |
| Batch buffer (1000 FileInfo) | ~1 MB | Flushed every batch |
| ProgressReporter queues | ~1 MB | Bounded queues (max 100 events) |
| SQLAlchemy identity map | ~10 MB | Flushed after each batch |
| **Total RSS (estimated)** | **~160 MB** | Well under 500MB |

The mmap region is virtual address space, not RSS. Actual physical memory depends on access patterns, typically 50-100MB of the mapped region is resident.

# Observability & Logging Standards

**Reference:** ADR-010 (Structured Logging with structlog)  
**Date:** 2026-06-23

---

## 1. Logging Architecture

### 1.1 Technology Choice

- **Library**: `structlog` (v24.4+)
- **Output**: JSON in production, colored console in development
- **Transport**: stdout (Docker-native, compatible with any log aggregation)
- **Correlation**: `contextvars`-based request/task correlation IDs

### 1.2 Pipeline Configuration

```python
# Development mode
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
)

# Production mode
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
```

---

## 2. Structured Log Format

### 2.1 Standard Fields (Every Log Entry)

```json
{
    "timestamp": "2026-06-23T10:05:00.123456Z",
    "level": "info",
    "event": "scan_batch_committed",
    "correlation_id": "req-abc123",
    "logger": "spaceai.services.scanner_service",
    "environment": "production"
}
```

| Field | Type | Source | Required |
|-------|------|--------|----------|
| `timestamp` | ISO8601 string | structlog TimeStamper | Yes |
| `level` | string | structlog add_log_level | Yes |
| `event` | string | Developer-provided | Yes |
| `correlation_id` | UUID string | Middleware / Task Manager | Yes |
| `logger` | string | Module name | Yes |
| `environment` | string | Config (dev/prod) | Yes |

### 2.2 Context-Specific Fields

Added by the calling code (not the logging framework):

```json
{
    "event": "scan_batch_committed",
    "scan_id": "uuid-of-scan",
    "batch_size": 1000,
    "files_so_far": 45000,
    "batch_duration_ms": 23.4,
    "directory": "/Users/vamsig/projects"
}
```

---

## 3. Log Level Standards

| Level | Usage | Examples |
|-------|-------|----------|
| **DEBUG** | Internal state useful for debugging. Never in production logs by default. | Thread pool status, SQL query parameters, individual file processing |
| **INFO** | Key business events. Answers "what happened?" | Scan started, scan completed, recommendation generated, cleanup executed |
| **WARNING** | Recoverable issues that may need attention | Permission denied (skipped file), AI provider timeout (circuit breaker opened), slow query detected |
| **ERROR** | Failures that need investigation | Scan failed, DB write error, cleanup rollback triggered, unhandled exception |
| **CRITICAL** | System cannot continue | Database corrupted, disk full, cannot write to trash directory |

### 3.1 Log Level Configuration

- **Development**: DEBUG (all levels visible)
- **Production**: INFO (debug filtered out)
- **Configurable** via `SPACEAI_LOG_LEVEL` environment variable

---

## 4. Correlation ID Propagation

### 4.1 HTTP Request Correlation

```python
# Middleware: assigns correlation_id per request
@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    
    structlog.contextvars.unbind_contextvars("correlation_id")
    return response
```

### 4.2 Background Task Correlation

```python
# TaskManager: creates new correlation_id for each task
async def submit(self, task_type, task_fn, **kwargs) -> str:
    task_id = str(uuid.uuid4())
    correlation_id = f"task-{task_id}"
    
    async def wrapped():
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            task_id=task_id,
            task_type=task_type.value,
        )
        await task_fn(...)
    
    asyncio.create_task(wrapped())
    return task_id
```

### 4.3 Cross-Boundary Correlation

When an API request triggers a background task:
```json
// API request log
{"event": "scan_requested", "correlation_id": "req-abc123", "spawned_task_id": "task-xyz789"}

// Background task log  
{"event": "scan_started", "correlation_id": "task-xyz789", "parent_correlation_id": "req-abc123"}
```

---

## 5. Domain-Specific Log Events

### 5.1 Scanner Events

| Event | Level | Fields |
|-------|-------|--------|
| `scan_started` | INFO | scan_id, root_path, scan_type, exclusion_count |
| `scan_directory_entered` | DEBUG | scan_id, directory, depth |
| `scan_batch_committed` | DEBUG | scan_id, batch_size, files_so_far, batch_duration_ms |
| `scan_checkpoint_saved` | INFO | scan_id, files_so_far, last_directory |
| `scan_file_skipped` | WARNING | scan_id, path, reason (permission denied, broken symlink) |
| `scan_completed` | INFO | scan_id, total_files, total_dirs, total_bytes, duration_seconds, files_per_second |
| `scan_failed` | ERROR | scan_id, error_type, error_message, files_scanned_before_failure |
| `scan_cancelled` | INFO | scan_id, files_so_far, cancelled_by |
| `scan_resumed` | INFO | scan_id, checkpoint_files, resumed_from_directory |

### 5.2 Duplicate Detection Events

| Event | Level | Fields |
|-------|-------|--------|
| `hash_pass_started` | INFO | scan_id, candidate_count (files needing hash) |
| `hash_batch_completed` | DEBUG | scan_id, batch_size, hashed_so_far |
| `duplicate_group_found` | INFO | group_id, hash, file_count, wasted_bytes |
| `hash_pass_completed` | INFO | scan_id, total_hashed, groups_found, total_wasted |

### 5.3 AI Provider Events

| Event | Level | Fields |
|-------|-------|--------|
| `ai_request_started` | INFO | provider, model, prompt_tokens_estimate |
| `ai_request_completed` | INFO | provider, model, duration_ms, response_tokens |
| `ai_request_failed` | WARNING | provider, model, error_type, retry_count |
| `ai_circuit_opened` | WARNING | provider, failure_count, open_duration_seconds |
| `ai_circuit_closed` | INFO | provider, recovery_call_duration_ms |

### 5.4 Cleanup Events

| Event | Level | Fields |
|-------|-------|--------|
| `cleanup_proposed` | INFO | action_id, action_type, target_count, total_bytes |
| `cleanup_approved` | INFO | action_id, approved_by |
| `cleanup_executed` | INFO | action_id, files_processed, bytes_recovered, trash_location |
| `cleanup_failed` | ERROR | action_id, error_type, files_processed_before_failure |
| `cleanup_rolled_back` | WARNING | action_id, files_restored, trigger (user/error) |

### 5.5 API Events

| Event | Level | Fields |
|-------|-------|--------|
| `request_started` | DEBUG | method, path, query_params |
| `request_completed` | INFO | method, path, status_code, duration_ms |
| `request_failed` | ERROR | method, path, status_code, error_type, error_message |
| `slow_request` | WARNING | method, path, duration_ms (threshold: 500ms) |

---

## 6. Metrics

### 6.1 Application Metrics (In-Process)

No external metrics system (Prometheus/Grafana) for Phase 1. Metrics are captured as structured log events and optionally stored in a `metrics` table for dashboard display.

| Metric | Type | Collection |
|--------|------|------------|
| `scan_duration_seconds` | Gauge | Per scan, logged on completion |
| `scan_files_per_second` | Gauge | Per scan, logged on completion |
| `scan_memory_peak_mb` | Gauge | Sampled during scan (via `tracemalloc`) |
| `api_request_duration_ms` | Histogram | Per request, via middleware |
| `api_requests_total` | Counter | Per endpoint, via middleware |
| `api_errors_total` | Counter | Per endpoint + status code |
| `ai_request_duration_ms` | Histogram | Per provider call |
| `ai_circuit_state` | Gauge | closed=0, half_open=1, open=2 |
| `db_query_duration_ms` | Histogram | Via SQLAlchemy event hooks |
| `task_queue_size` | Gauge | Number of pending/running tasks |

### 6.2 Health Endpoint Metrics

```
GET /api/v1/health
```

```json
{
    "status": "healthy",
    "uptime_seconds": 3600,
    "version": "0.1.0",
    "database": {
        "status": "connected",
        "size_bytes": 425000000,
        "wal_size_bytes": 12000000
    },
    "tasks": {
        "running": 1,
        "pending": 0,
        "completed_last_hour": 3
    },
    "ai_provider": {
        "configured": "openai",
        "circuit_state": "closed",
        "last_success": "2026-06-23T10:00:00Z"
    }
}
```

### 6.3 Scan Telemetry (Stored)

After each scan completes, telemetry is saved for trend analysis:

```sql
-- Stored in audit_logs with action='scan_telemetry'
{
    "scan_id": "uuid",
    "root_path": "/Users/vamsig",
    "total_files": 892341,
    "total_bytes": 214748364800,
    "duration_seconds": 87,
    "files_per_second": 10257.9,
    "peak_memory_mb": 145,
    "batch_count": 893,
    "errors_skipped": 23,
    "platform": "macos"
}
```

---

## 7. Error Tracking

### 7.1 Unhandled Exception Handler

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        error_type=type(exc).__name__,
        error_message=str(exc),
        path=request.url.path,
        method=request.method,
        traceback=traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}},
    )
```

### 7.2 Error Classification

| Category | Action | Example |
|----------|--------|---------|
| **Client Error (4xx)** | Log at INFO | Validation failed, resource not found |
| **Transient Error** | Log at WARNING, retry | AI timeout, DB busy |
| **Persistent Error** | Log at ERROR, alert | DB corruption, disk full |
| **Critical Error** | Log at CRITICAL, stop | Cannot open database file |

---

## 8. Performance Monitoring

### 8.1 Slow Query Detection

```python
# SQLAlchemy event hook
@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, ...):
    conn.info["query_start"] = time.time()

@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, ...):
    duration_ms = (time.time() - conn.info["query_start"]) * 1000
    if duration_ms > 100:  # 100ms threshold
        logger.warning("slow_query", duration_ms=duration_ms, statement=statement[:200])
```

### 8.2 Memory Monitoring (During Scans)

```python
import tracemalloc

# Sample memory every 10,000 files during scan
if files_scanned % 10000 == 0:
    current, peak = tracemalloc.get_traced_memory()
    logger.debug("memory_sample", 
                 current_mb=current / 1024 / 1024, 
                 peak_mb=peak / 1024 / 1024)
    
    if peak / 1024 / 1024 > 400:  # 400MB warning threshold (500MB is NFR limit)
        logger.warning("memory_pressure", peak_mb=peak / 1024 / 1024)
```

---

## 9. Log Rotation & Retention

### 9.1 Docker/Production

- Logs go to stdout (no file rotation needed — Docker handles it).
- Docker log driver handles rotation: `max-size: 10m`, `max-file: 3`.

### 9.2 Local Development

- Logs go to console (colored, human-readable).
- No file output in development.
- Optional: `SPACEAI_LOG_FILE=/tmp/spaceai.log` for file output.

### 9.3 Audit Log Retention

- `audit_logs` table entries are never deleted.
- Table grows ~3MB/year at expected usage rates.
- Pruning (if needed): archive entries older than 2 years.

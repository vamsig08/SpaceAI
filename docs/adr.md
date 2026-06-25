# Architecture Decision Records

Whenever an architectural decision is made, update this document.

Document:
- Decision
- Reasoning
- Alternatives considered
- Tradeoffs

---

## ADR-001: Clean Architecture with Repository Pattern and Service Layer

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Adopt Clean Architecture with strict layering:

```
API Routes → Services → Repositories → Database
                ↓
            AI Providers
```

- **API Layer**: Request validation, response serialization, HTTP concerns only. Zero business logic.
- **Service Layer**: All business logic, orchestration, domain rules.
- **Repository Layer**: Database access abstraction. One repository per aggregate root.
- **Schema Layer**: Pydantic models for request/response validation (separate from ORM models).
- **Model Layer**: SQLAlchemy ORM models (internal, never exposed to API consumers).

### Reasoning

- Mandated by engineering standards (repository pattern required, service layer required, no business logic in API routes).
- Enables testing services with mock repositories (fast unit tests without DB).
- Enables swapping SQLite → PostgreSQL by changing only the repository implementations and database engine config.
- SOLID compliance: Single Responsibility (each layer does one thing), Dependency Inversion (services depend on repository abstractions).

### Alternatives Considered

1. **Active Record pattern** — Simpler but couples business logic to DB models. Violates SOLID.
2. **CQRS** — Overkill for a local-first application. Adds complexity without proportional benefit at this scale.
3. **Hexagonal Architecture** — Similar to Clean Architecture but adds port/adapter formalism that doesn't add value here.

### Tradeoffs

- More boilerplate (repository + service + schema per entity).
- Slower initial development velocity.
- **Accepted because**: testability, maintainability, and DB-agnosticism are worth the upfront cost for a 14-phase project.

---

## ADR-002: SQLite with WAL Mode as Primary Database

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Use SQLite in WAL (Write-Ahead Logging) mode as the primary database with the following configuration:

```sql
PRAGMA journal_mode=WAL;
PRAGMA cache_size=-65536;       -- 64MB page cache
PRAGMA busy_timeout=5000;       -- 5 second busy retry
PRAGMA synchronous=NORMAL;      -- Balanced durability/performance
PRAGMA temp_store=MEMORY;       -- Temp tables in RAM
PRAGMA mmap_size=268435456;     -- 256MB memory-mapped I/O
```

Design the repository layer so that swapping to PostgreSQL requires only:
1. Changing the SQLAlchemy engine URL.
2. Adjusting any SQLite-specific pragmas/extensions.
3. Re-running Alembic migrations.

### Reasoning

- Mandated by architecture steering (SQLite).
- WAL mode enables concurrent reads during writes — critical when the scanner is writing and the API is reading.
- Zero external dependencies for local-first deployment.
- Single-file database simplifies backup, migration, and Docker volume mounting.
- Memory-mapped I/O enables sub-200ms query responses for cached data (NFR requirement).
- `busy_timeout` prevents immediate lock errors during concurrent access.

### Alternatives Considered

1. **PostgreSQL from day one** — Better concurrency but adds infrastructure dependency. Violates "Docker deployment in one command" simplicity.
2. **DuckDB** — Excellent for analytics but poor write concurrency. Scanning requires heavy concurrent writes.
3. **SQLite without WAL** — Exclusive locking would serialize all reads during scan writes. Unacceptable.

### Tradeoffs

- SQLite has no true concurrent writes (WAL allows one writer + many readers).
- No built-in full-text search (can add via FTS5 extension if needed).
- No network access (local-only, which is fine for local-first).
- Limited to ~281 TB database size (not a practical concern).
- **Accepted because**: local-first deployment, NFR for single-command Docker, and WAL mode solves the read/write concurrency problem.

---

## ADR-003: In-Process Async Task Manager for Background Work

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Implement an in-process background task system using Python's `asyncio` with a thread pool executor for CPU-bound filesystem operations. No external task queue (Celery, ARQ, Redis) for Phase 1.

Architecture:

```
┌─────────────────────────────────────────────────────┐
│                   FastAPI Process                     │
│                                                       │
│  ┌──────────┐    ┌──────────────┐    ┌────────────┐ │
│  │ API Route│───▶│ Task Manager │───▶│  Task Pool  │ │
│  └──────────┘    └──────┬───────┘    └─────┬──────┘ │
│                         │                   │        │
│                  ┌──────▼───────┐    ┌──────▼──────┐ │
│                  │ Task Registry│    │Thread Pool   │ │
│                  │ (state, meta)│    │Executor      │ │
│                  └──────────────┘    │(os.scandir,  │ │
│                                      │ os.stat,     │ │
│                                      │ hashlib)     │ │
│                                      └─────────────┘ │
└─────────────────────────────────────────────────────┘
```

Components:
- **TaskManager**: Singleton that tracks all running/completed/failed tasks. Provides lifecycle methods (start, cancel, status).
- **Task Registry**: In-memory dict mapping task_id → TaskState (status, progress, errors, timestamps).
- **ThreadPoolExecutor**: `max_workers=4` for filesystem I/O (os.scandir, os.stat, hashlib). These are CPU/IO-bound and cannot be async.
- **AsyncIO coordination**: Main event loop dispatches work to thread pool via `asyncio.loop.run_in_executor()`.

Concurrency rules:
- Maximum 1 scan task at a time (configurable).
- Maximum 1 hash task at a time.
- Analytics/recommendation tasks can run concurrently with scans.
- All tasks are cancellable via `asyncio.Event` signaling.

### Reasoning

- No external dependencies (Redis, RabbitMQ) — supports "Docker deployment in one command" NFR.
- Sufficient for local-first single-user operation.
- Thread pool handles the GIL limitation for `os.scandir`/`os.stat` (these release the GIL during syscalls).
- Task state survives within process lifetime. Scan checkpointing (ADR-005) handles crash recovery.

### Alternatives Considered

1. **Celery + Redis** — Production-grade but requires Redis infrastructure. Overkill for single-user local app.
2. **ARQ (async Redis queue)** — Lighter than Celery but still needs Redis.
3. **Dramatiq** — Same Redis dependency issue.
4. **multiprocessing** — Shared memory complexity, harder to coordinate with FastAPI event loop.

### Tradeoffs

- Tasks lost on process restart (mitigated by scan checkpointing).
- No distributed execution (fine for local-first).
- Single-process limits throughput to one machine's cores (acceptable for target use case).
- **Accepted because**: zero infrastructure dependencies, sufficient for single-user local operation, and checkpoint recovery handles the crash scenario.

---

## ADR-004: Server-Sent Events (SSE) for Real-Time Progress

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Use Server-Sent Events (SSE) via FastAPI's `StreamingResponse` for all real-time progress reporting. No WebSockets.

Protocol:

```
Client                              Server
  │                                    │
  │  GET /api/v1/scans/{id}/progress   │
  │  Accept: text/event-stream         │
  │───────────────────────────────────▶│
  │                                    │
  │  HTTP 200 OK                       │
  │  Content-Type: text/event-stream   │
  │◀───────────────────────────────────│
  │                                    │
  │  event: progress                   │
  │  data: {"files_scanned": 1000}     │
  │◀───────────────────────────────────│
  │                                    │
  │  event: progress                   │
  │  data: {"files_scanned": 5000}     │
  │◀───────────────────────────────────│
  │                                    │
  │  event: completed                  │
  │  data: {"total_files": 892341}     │
  │◀───────────────────────────────────│
  │                                    │
  │  (connection closes)               │
  │◀───────────────────────────────────│
```

Event types:
- `progress` — Periodic update (every 1 second or every 1000 files, whichever comes first)
- `checkpoint` — Scan checkpoint saved (enables resume)
- `error` — Non-fatal error during scan
- `completed` — Scan finished successfully
- `failed` — Scan failed fatally
- `cancelled` — Scan was cancelled by user

Frontend integration:
- Use `EventSource` API or `@tanstack/react-query` with SSE adapter.
- Auto-reconnect with `Last-Event-ID` header for resume.

### Reasoning

- SSE is unidirectional (server → client), which matches the progress reporting use case exactly.
- Native browser support via `EventSource` — no library needed on frontend.
- Simpler than WebSockets (no connection upgrade, no bidirectional protocol).
- Works through HTTP proxies and load balancers without special configuration.
- FastAPI supports SSE natively via `StreamingResponse` with async generators.

### Alternatives Considered

1. **WebSockets** — Bidirectional, but we don't need client→server messages for progress. Adds protocol complexity.
2. **Polling** — Simple but wastes bandwidth, adds latency, and doesn't satisfy real-time UX expectations.
3. **Long polling** — Awkward to implement, no real advantage over SSE.

### Tradeoffs

- SSE is HTTP/1.1 only (per spec), limited to ~6 connections per domain in browsers. Not an issue for a single-user local app.
- No binary data support (JSON text only — fine for progress events).
- **Accepted because**: perfect fit for unidirectional progress streams, zero dependencies, native browser support.

---

## ADR-005: Multi-Pass Scanning with Checkpoint Recovery

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Implement filesystem scanning as a multi-pass pipeline with checkpoint-based crash recovery:

**Pass 1 — Discovery (fast, metadata only):**
- Walk directory tree using `os.scandir()` (3-5x faster than `os.walk()`)
- Collect: path, size, timestamps, extension
- Batch insert into `files` table (1000 records per batch)
- Checkpoint every 10,000 files (save last-completed directory path to `scans` table)
- Target: 1M files in < 15 minutes (leaving headroom for 30-min NFR)

**Pass 2 — Enrichment (deferred, on-demand):**
- MIME type detection (only when user views file details or for category assignment)
- SHA256 hashing (only for duplicate detection, triggered by Phase 3)
- Stale file scoring (triggered by Phase 4)

**Checkpoint Recovery:**
- On scan start, check for incomplete scans (status = 'running' from a previous crash).
- If found, offer resume: skip directories already recorded, continue from last checkpoint.
- Checkpoint data stored in `scans.checkpoint_data` (JSON: `{"last_directory": "/path", "files_so_far": 45000}`).

**Memory Management (< 500MB NFR):**
- Generator-based directory traversal (never build full file list in memory).
- Batch writer holds at most 1000 `FileInfo` objects (~200KB).
- Thread pool workers: 4 threads × ~1KB stack per file operation = negligible.
- SQLAlchemy session: flush and expire after each batch to prevent identity map growth.
- Peak memory budget: ~50MB for scanner + ~64MB SQLite page cache + overhead.

**Performance Budget (1M files in 30 min NFR):**
```
Target: 1,000,000 files / 1,800 seconds = 556 files/second minimum
With 4 threads: 556 / 4 = 139 files/thread/second
Per-file budget: ~7ms (os.scandir + os.stat + batch buffer)
Achievable: os.scandir + os.stat typically < 1ms on SSD
```

**Cross-Platform (Windows/macOS/Linux NFR):**
- Use `pathlib.Path` for all path operations (handles separators).
- Use `os.scandir()` which is optimized per platform.
- Skip permission/owner on Windows (not POSIX-compatible).
- Handle `PermissionError` gracefully (log and skip).
- Handle long paths on Windows (> 260 chars) via `\\?\` prefix.

### Reasoning

- Multi-pass separates the fast (discovery) from the slow (hashing). Users see results immediately after Pass 1.
- Checkpoint recovery satisfies "recovery from interrupted scans" NFR.
- Generator-based traversal satisfies "memory under 500MB" NFR.
- `os.scandir()` returns `DirEntry` objects with cached `stat()` results — avoids double syscalls.

### Alternatives Considered

1. **Single-pass with full metadata** — Slower (MIME detection is expensive per file). Users wait longer for first results.
2. **File system watcher (inotify/FSEvents)** — Good for incremental updates but doesn't help initial scan. Added as future enhancement.
3. **Parallel directory scanning with work-stealing** — Complex, and os.scandir is already fast enough with 4 threads to meet the 30-min NFR.

### Tradeoffs

- Multi-pass means duplicate detection requires a second scan pass (acceptable, it's a separate phase).
- Checkpoint granularity is per-directory, not per-file (a directory with 100K files would be re-scanned on crash). Acceptable because such directories are rare.
- **Accepted because**: satisfies all three critical NFRs (time, memory, recovery) while keeping implementation manageable.

---

## ADR-006: Cross-Platform File System Abstraction

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Create a platform abstraction layer that normalizes filesystem differences across Windows, macOS, and Linux:

```python
# scanner/platform.py
class PlatformInfo:
    """Detects and exposes platform-specific behavior."""
    
    @property
    def path_separator(self) -> str: ...
    
    @property  
    def supports_posix_permissions(self) -> bool: ...
    
    @property
    def max_path_length(self) -> int: ...
    
    def normalize_path(self, path: str) -> str: ...
    
    def get_file_owner(self, path: Path) -> str | None: ...
    
    def get_default_exclusions(self) -> list[str]: ...
    
    def get_trash_directory(self) -> Path: ...
```

Platform-specific decisions:

| Feature | macOS | Linux | Windows |
|---------|-------|-------|---------|
| Path normalization | POSIX as-is | POSIX as-is | Convert `\` → `/` in DB, use `\\?\` prefix for long paths |
| Permissions | Full POSIX (octal) | Full POSIX (octal) | Store as `None` (not meaningful) |
| Owner | `pwd.getpwuid()` | `pwd.getpwuid()` | `None` (requires Win32 API) |
| MIME detection | `python-magic` (libmagic) | `python-magic` (libmagic) | `mimetypes` stdlib fallback (no libmagic) |
| Trash location | `~/.Trash` | `~/.local/share/Trash` (XDG) | `Send to Recycle Bin` via `send2trash` |
| Docker socket | `/var/run/docker.sock` | `/var/run/docker.sock` | `npipe:////./pipe/docker_engine` |
| Default exclusions | `/System`, `/Library`, `.Spotlight-V100` | `/proc`, `/sys`, `/dev` | `C:\Windows`, `C:\$Recycle.Bin` |

### Reasoning

- "Support Windows, macOS, Linux" is an explicit NFR.
- Centralizing platform differences in one module prevents platform-specific `if` statements scattered across the codebase.
- Using `pathlib.Path` as the primary path type gives cross-platform behavior by default.
- Fallback for MIME detection on Windows avoids the `libmagic` dependency issue (libmagic requires separate binary installation on Windows).

### Alternatives Considered

1. **Ignore Windows** — Violates NFR.
2. **Use `os.path` everywhere** — Works but `pathlib` is more ergonomic and type-safe.
3. **Require WSL for Windows** — Limits audience, poor developer experience.

### Tradeoffs

- Some features degrade on Windows (no POSIX permissions, less accurate MIME detection).
- Trash behavior differs per platform (macOS Trash vs XDG vs Recycle Bin).
- Testing requires CI on all three platforms or careful mocking.
- **Accepted because**: NFR is explicit, and the abstraction layer contains the complexity.

---

## ADR-007: AI Provider Abstraction with Circuit Breaker

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Implement a provider abstraction with the Strategy pattern and a circuit breaker for resilience:

```python
class AIProvider(Protocol):
    """Abstract interface for AI providers."""
    
    async def generate_recommendations(
        self, context: AnalysisContext
    ) -> list[Recommendation]: ...
    
    async def generate_summary(
        self, context: AnalysisContext
    ) -> str: ...
    
    async def analyze_workspace(
        self, workspace: WorkspaceContext
    ) -> WorkspaceAnalysis: ...

class CircuitBreaker:
    """Wraps provider calls with failure detection."""
    
    # States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing)
    # Thresholds: 3 failures → open for 60 seconds
    # On OPEN: return cached result or raise gracefully
```

Provider selection:
- Configured via environment variable `SPACEAI_AI_PROVIDER=openai|ollama`
- Factory pattern creates the appropriate provider at startup.
- Both providers implement the same `AIProvider` protocol.

Circuit breaker behavior:
- **Closed** (normal): Pass calls through to provider.
- **Open** (after 3 consecutive failures): Return cached recommendations or a structured "AI unavailable" response. Duration: 60 seconds.
- **Half-open** (after timeout): Allow one call through. If it succeeds, close the circuit. If it fails, re-open.

Retry policy:
- 3 retries with exponential backoff (1s, 2s, 4s).
- Timeout per call: 30 seconds for OpenAI, 60 seconds for Ollama (local model loading).
- Retry only on transient errors (timeout, 429, 5xx). Do not retry on 4xx.

### Reasoning

- Architecture steering mandates provider abstraction with OpenAI and Ollama.
- LLM APIs are inherently unreliable (rate limits, timeouts, model loading delays).
- Circuit breaker prevents cascade failures and provides graceful degradation.
- The application must remain functional even when AI is unavailable (non-AI features work independently).

### Alternatives Considered

1. **No circuit breaker, just retries** — Users experience long waits when provider is down. Bad UX.
2. **Queue-based AI calls** — Adds complexity for marginal benefit in a single-user app.
3. **LangChain abstraction** — Heavy dependency, abstracts too much, version instability.

### Tradeoffs

- Circuit breaker adds complexity to the AI module.
- Cached/stale recommendations may be shown during outages (clearly labeled in UI).
- **Accepted because**: system must never appear broken due to an external dependency failure.

---

## ADR-008: Safety-First Cleanup with Trash Pattern

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

All destructive operations follow a mandatory multi-step approval workflow. No file is ever permanently deleted by default.

Workflow:

```
[1] Propose     → cleanup_actions.status = 'proposed'
[2] Review      → User views affected files, sizes, risks
[3] Approve     → cleanup_actions.status = 'approved', approved_at = now()
[4] Dry Run     → Execute without side effects, report what would happen
[5] Execute     → Move to trash (never delete), status = 'completed'
[6] Rollback    → Restore from trash if needed (within retention period)
```

Trash architecture:
- Location: `~/.spaceai/trash/{YYYY-MM-DD}/{action_id}/`
- Original paths preserved in a manifest file: `~/.spaceai/trash/{date}/{action_id}/manifest.json`
- Manifest records: original path, size, hash, timestamp moved.
- Retention: 30 days default (configurable). Auto-purge after retention.
- Platform-specific: Use system trash where possible (via `send2trash` library), fall back to SpaceAI-managed trash.

Safety invariants (enforced at service layer):
1. No code path can delete a file without an `approved` cleanup_action record.
2. Dry-run mode is always available and produces identical output minus actual file operations.
3. Every executed action creates an audit_log entry.
4. Rollback is always available within retention period.
5. Batch operations are atomic: if any file in a batch fails, the entire batch rolls back.

### Reasoning

- Requirements explicitly state: "The system must never delete anything automatically. All actions require user approval."
- "Every action must be reversible" — trash pattern guarantees reversibility.
- Audit trail satisfies compliance and trust requirements.
- Manifest-based trash enables restore without database (resilience if DB corrupts).

### Alternatives Considered

1. **Direct deletion with undo window** — Risky. Once deleted, files are gone.
2. **System Recycle Bin only** — No manifest, can't restore programmatically with path preservation.
3. **Soft-delete in DB (mark as deleted, leave on disk)** — Doesn't reclaim space.

### Tradeoffs

- Trash consumes temporary disk space (mitigated by 30-day auto-purge).
- Two-step workflow is slower than one-click delete (intentional — safety over speed).
- **Accepted because**: safety is a non-negotiable requirement, and user trust depends on reversibility.

---

## ADR-009: Pre-Computed Analytics with Snapshot Tables

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Maintain pre-computed analytics in dedicated snapshot tables, refreshed on scan completion. API queries read from snapshots (fast) rather than computing from raw `files` table (slow).

Snapshot strategy:

| Query Type | Source Table | Refresh Trigger | Target Response Time |
|------------|-------------|-----------------|---------------------|
| Storage overview | `storage_snapshots` | Post-scan | < 50ms |
| Category breakdown | `storage_snapshots.category_breakdown` (JSON) | Post-scan | < 50ms |
| Top files/folders | `files` / `folders` with indexes | Live query with index | < 200ms |
| Growth history | `storage_snapshots` (time series) | Post-scan | < 100ms |
| Duplicate summary | `duplicate_groups` aggregate | Post-hash-pass | < 100ms |

Refresh mechanism:
- On scan completion, a `PostScanHook` runs in the task manager.
- Computes aggregates and upserts into `storage_snapshots`.
- Indexed queries (top N files, folders by size) run against the raw table with covering indexes — fast enough without materialization.

Caching layer:
- In-memory LRU cache (Python `functools.lru_cache` or `cachetools.TTLCache`) for the most-hit endpoints.
- TTL: 60 seconds (invalidated on scan completion).
- No Redis required (consistent with zero-dependency local deployment).

### Reasoning

- NFR requires "API response time under 200ms for cached queries."
- Aggregating 1M rows on every dashboard load would take 500ms+ even with indexes.
- Snapshot tables trade storage space (~1KB per snapshot) for query speed.
- In-memory cache avoids even the SQLite round-trip for repeated identical queries.

### Alternatives Considered

1. **Redis cache** — Fast but adds infrastructure dependency.
2. **Compute on every request** — Violates 200ms NFR for large datasets.
3. **SQLite triggers for materialized views** — Complex, harder to debug, and triggers add write overhead during scan inserts.

### Tradeoffs

- Snapshot data is slightly stale (only refreshed on scan completion). Acceptable because data changes only during scans.
- Additional storage (~1KB per snapshot × 365 days = 365KB/year). Negligible.
- **Accepted because**: directly satisfies the 200ms NFR with zero external dependencies.

---

## ADR-010: Structured Logging with structlog and Correlation IDs

**Status:** Accepted  
**Date:** 2026-06-23

### Decision

Use `structlog` for all application logging with JSON output in production and human-readable colored output in development.

Standards:

```python
# Every log entry includes:
{
    "timestamp": "2026-06-23T10:05:00.123Z",   # ISO8601
    "level": "info",                             # debug|info|warning|error|critical
    "event": "scan_progress",                    # Machine-readable event name
    "correlation_id": "uuid-of-request",         # Traces across async boundaries
    "scan_id": "uuid",                           # Context-specific fields
    "files_scanned": 45000,
    "current_directory": "/Users/vamsig/projects"
}
```

Logging architecture:

```
┌─────────────────────────────────────────┐
│              Application                 │
│                                          │
│  ┌──────────┐  ┌──────────┐            │
│  │ API Mid  │  │ Services │            │
│  │ (adds    │  │ (domain  │            │
│  │  corr_id)│  │  events) │            │
│  └────┬─────┘  └────┬─────┘            │
│       │              │                   │
│       ▼              ▼                   │
│  ┌─────────────────────────────────┐    │
│  │        structlog pipeline        │    │
│  │  [add_timestamp]                 │    │
│  │  [add_log_level]                 │    │
│  │  [filter_by_level]               │    │
│  │  [render_json | render_console]  │    │
│  └──────────────────┬──────────────┘    │
│                     │                    │
│                     ▼                    │
│              stdout / file               │
└─────────────────────────────────────────┘
```

Log levels usage:
- **DEBUG**: Detailed internal state (batch write counts, thread pool status).
- **INFO**: Key business events (scan started, scan completed, recommendation generated).
- **WARNING**: Recoverable issues (permission denied on file, AI provider timeout).
- **ERROR**: Failures requiring attention (scan failed, DB write error).
- **CRITICAL**: System cannot continue (DB corrupted, no disk space).

Correlation ID:
- Generated per API request in middleware.
- Propagated to all service calls and background tasks via `contextvars`.
- Enables tracing a single user action across async boundaries.

Metrics (Phase 12 — lightweight, no Prometheus dependency):
- Scan duration, files/second throughput.
- API response times (middleware-measured).
- Error counts by category.
- Stored in `scan_telemetry` or logged as structured events (queryable via log aggregation).

### Reasoning

- Development rules require "always handle errors" — structured logging is the foundation of error observability.
- JSON logs are machine-parseable for future integration with log aggregation (ELK, CloudWatch).
- Correlation IDs enable debugging async workflows where a scan triggers multiple background operations.
- `structlog` is the standard choice for Python structured logging (fast, composable, well-maintained).

### Alternatives Considered

1. **Python stdlib `logging`** — Works but verbose configuration, no built-in JSON formatting, no context binding.
2. **loguru** — Nice API but less composable pipeline, global state concerns.
3. **OpenTelemetry** — Full observability stack is overkill for Phase 1. Can add later as traces/metrics layer.

### Tradeoffs

- `structlog` has a learning curve vs plain `logging`.
- JSON logs are less readable in terminal (mitigated by dev-mode console renderer).
- **Accepted because**: production-quality logging is non-negotiable for a system that manages user data.

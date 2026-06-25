# SpaceAI — Architecture Design Review

**Author:** Staff Software Engineer (Design Review)  
**Date:** 2026-06-23  
**Status:** Pre-Implementation Review

---

## 1. Gaps, Risks, Scalability Concerns & Contradictions

### 1.1 Contradictions

| Item | Source A | Source B | Issue |
|------|----------|----------|-------|
| Database tables | Steering lists: `files`, `folders`, `duplicates`, `scan_history`, `recommendations` | Requirements mention: predictions, cleanup audit, developer workspaces, stale files | Steering schema is incomplete — missing 5+ tables needed for phases 4-12 |
| Async requirement | Standards say "use async where appropriate" | SQLAlchemy with SQLite has limited async support (SQLite doesn't support true async I/O) | Need async SQLAlchemy with `aiosqlite` driver, or accept sync for DB-bound ops |
| PostgreSQL later | Requirements say "SQLite initially, PostgreSQL support later" | Architecture steering only mentions SQLite | Must design the repository layer to be DB-agnostic from day one |

### 1.2 Architectural Gaps

| Gap | Impact | Severity |
|-----|--------|----------|
| **No background task system** | Filesystem scans of 1M+ files cannot run synchronously in an HTTP request. No mention of Celery, ARQ, or any task queue. | Critical |
| **No WebSocket/SSE design** | Scan progress, real-time status updates need push mechanism. Not mentioned anywhere. | High |
| **No caching layer** | Analytics aggregations over millions of rows will be slow without caching. No Redis/in-memory cache mentioned. | High |
| **No rate limiting / auth** | API has no authentication model. Even for local-first, multi-user Docker deployment needs consideration. | Medium |
| **No file system watcher** | Incremental scanning requires detecting changes between scans. No inotify/FSEvents/watchdog integration mentioned. | Medium |
| **No migration strategy** | Alembic not mentioned. Schema will evolve across 14 phases. | High |
| **No error recovery for AI calls** | LLM calls fail frequently. No retry/circuit-breaker pattern specified. | Medium |
| **Roadmap is skeletal** | `docs/roadmap.md` has no detail for phases 4-14. | Low (documentation gap) |

### 1.3 Scalability Concerns

| Concern | Details |
|---------|---------|
| **SQLite write contention** | SQLite uses file-level locking. Multi-threaded scanner + API reads = lock contention. WAL mode helps but has limits. |
| **Memory during hashing** | SHA256 hashing 1M+ files must stream chunks, not load whole files. Needs explicit buffer size design. |
| **Frontend data volume** | Rendering 1M file records in a dashboard table requires virtual scrolling and server-side pagination. |
| **Analytics on cold storage** | Aggregating file stats over millions of rows per request is expensive. Need materialized views or pre-computed summaries. |
| **Docker image scanning** | Listing Docker images/containers requires Docker socket access — security implications in containerized deployment. |

### 1.4 Security Risks

| Risk | Mitigation Needed |
|------|------------------|
| Path traversal in scan endpoints | Validate and canonicalize all path inputs |
| Docker socket exposure | Run Docker analysis in a sandboxed sidecar |
| Arbitrary file deletion | The safety framework (Phase 9) must be bulletproof — double confirmation, audit trail, trash-first |
| API key exposure | AI provider keys must never reach the frontend; backend-only config |
| SQLite file permissions | Database file must not be world-readable |

### 1.5 Missing Non-Functional Requirements

- **Startup time**: How fast should the dashboard load with cold cache?
- **Scan throughput target**: No specific files/second target (suggest: 10,000+ files/sec)
- **Max concurrent scans**: Single scan? Multiple?
- **Data retention**: How long to keep historical snapshots?
- **Disk budget for DB**: SQLite DB for 1M files metadata ~ 200-500MB. Acceptable?

---

## 2. Proposed Improvements

### 2.1 Add Background Task Infrastructure

Use **ARQ** (async Redis queue) or **built-in asyncio task manager** for local-first operation without Redis dependency. For Phase 1, use an in-process background task runner with `asyncio.create_task` and a task registry. Graduate to ARQ when PostgreSQL/Redis is added.

### 2.2 Add Server-Sent Events (SSE) for Progress

FastAPI supports SSE natively via `StreamingResponse`. Use this for:
- Scan progress (files scanned, current directory, ETA)
- Cleanup operation progress
- AI recommendation generation progress

### 2.3 Add Pre-Computed Analytics Tables

Instead of querying raw file tables on every dashboard load, maintain:
- `storage_snapshots` — daily aggregated stats
- `category_breakdown` — pre-computed file type distributions
- `directory_sizes` — cached tree sizes

Refresh on scan completion via post-scan hooks.

### 2.4 Introduce Alembic for Migrations

Schema will evolve significantly across 14 phases. Alembic with auto-generation support ensures safe schema evolution and rollback capability.

### 2.5 Add Circuit Breaker for AI Calls

Wrap LLM provider calls in a circuit breaker pattern:
- 3 failures → open circuit for 60s
- Fallback to cached recommendations or graceful degradation
- Exponential backoff on retries

### 2.6 SQLite Configuration

Enable WAL mode, increase cache size, and set busy timeout:
```sql
PRAGMA journal_mode=WAL;
PRAGMA cache_size=-64000;  -- 64MB
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
```

### 2.7 Auth Strategy (Local-First)

For local-first, use a simple API key or session token. Design the middleware so it can be swapped for OAuth2/JWT when multi-user support arrives.

### 2.8 Scan Architecture Improvement

```
Scanner Process Flow:
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  API Route  │────▶│ Task Manager │────▶│   Scanner   │
│ POST /scan  │     │  (async)     │     │  Service    │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                 │
                    ┌──────────────┐     ┌───────▼──────┐
                    │  SSE Stream  │◀────│   Worker     │
                    │  /scan/status│     │  (threaded)  │
                    └──────────────┘     └──────┬───────┘
                                                │
                                         ┌──────▼──────┐
                                         │  Batch DB   │
                                         │  Writer     │
                                         └─────────────┘
```

Use batch inserts (1000 records/batch) to minimize SQLite lock contention.

---

## 3. Complete Directory Structure

### 3.1 Backend

```
backend/
├── alembic/
│   ├── versions/
│   ├── env.py
│   └── alembic.ini
├── app/
│   ├── __init__.py
│   ├── main.py                          # FastAPI app factory
│   ├── config.py                        # Settings via pydantic-settings
│   ├── dependencies.py                  # Dependency injection
│   │
│   ├── api/                             # API Layer (no business logic)
│   │   ├── __init__.py
│   │   ├── router.py                    # Root router aggregation
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── scans.py
│   │   │   ├── files.py
│   │   │   ├── folders.py
│   │   │   ├── duplicates.py
│   │   │   ├── analytics.py
│   │   │   ├── recommendations.py
│   │   │   ├── predictions.py
│   │   │   ├── cleanup.py
│   │   │   ├── workspaces.py
│   │   │   ├── developer_analysis.py
│   │   │   └── health.py
│   │   └── middleware/
│   │       ├── __init__.py
│   │       ├── error_handler.py
│   │       ├── request_logging.py
│   │       └── auth.py
│   │
│   ├── services/                        # Business Logic Layer
│   │   ├── __init__.py
│   │   ├── scanner_service.py
│   │   ├── analytics_service.py
│   │   ├── duplicate_service.py
│   │   ├── stale_file_service.py
│   │   ├── workspace_service.py
│   │   ├── developer_analysis_service.py
│   │   ├── recommendation_service.py
│   │   ├── prediction_service.py
│   │   ├── cleanup_service.py
│   │   └── audit_service.py
│   │
│   ├── repositories/                    # Repository Layer (DB access)
│   │   ├── __init__.py
│   │   ├── base.py                      # Abstract base repository
│   │   ├── file_repository.py
│   │   ├── folder_repository.py
│   │   ├── scan_repository.py
│   │   ├── duplicate_repository.py
│   │   ├── recommendation_repository.py
│   │   ├── prediction_repository.py
│   │   ├── cleanup_repository.py
│   │   ├── snapshot_repository.py
│   │   └── audit_repository.py
│   │
│   ├── models/                          # SQLAlchemy ORM Models
│   │   ├── __init__.py
│   │   ├── base.py                      # Declarative base, mixins
│   │   ├── file.py
│   │   ├── folder.py
│   │   ├── scan.py
│   │   ├── duplicate.py
│   │   ├── recommendation.py
│   │   ├── prediction.py
│   │   ├── cleanup_action.py
│   │   ├── audit_log.py
│   │   └── storage_snapshot.py
│   │
│   ├── schemas/                         # Pydantic Request/Response Models
│   │   ├── __init__.py
│   │   ├── common.py                    # Pagination, sorting, filters
│   │   ├── scan.py
│   │   ├── file.py
│   │   ├── folder.py
│   │   ├── duplicate.py
│   │   ├── analytics.py
│   │   ├── recommendation.py
│   │   ├── prediction.py
│   │   ├── cleanup.py
│   │   ├── workspace.py
│   │   └── developer_analysis.py
│   │
│   ├── core/                            # Cross-cutting concerns
│   │   ├── __init__.py
│   │   ├── database.py                  # Engine, session factory
│   │   ├── events.py                    # Application event bus
│   │   ├── exceptions.py               # Custom exception hierarchy
│   │   ├── logging.py                   # Structured logging setup
│   │   └── metrics.py                   # Observability metrics
│   │
│   ├── workers/                         # Background Tasks
│   │   ├── __init__.py
│   │   ├── task_manager.py             # Task registry & lifecycle
│   │   ├── scanner_worker.py
│   │   ├── hash_worker.py
│   │   └── analytics_worker.py
│   │
│   ├── ai/                              # AI Provider Abstraction
│   │   ├── __init__.py
│   │   ├── base_provider.py            # Abstract provider interface
│   │   ├── openai_provider.py
│   │   ├── ollama_provider.py
│   │   ├── provider_factory.py
│   │   ├── prompts/
│   │   │   ├── __init__.py
│   │   │   ├── recommendation.py
│   │   │   ├── workspace_analysis.py
│   │   │   └── summary.py
│   │   └── circuit_breaker.py
│   │
│   └── scanner/                         # Filesystem Scanner Engine
│       ├── __init__.py
│       ├── crawler.py                   # Directory traversal
│       ├── file_info.py                 # File metadata collection
│       ├── hasher.py                    # SHA256 streaming hasher
│       ├── exclusions.py               # Exclusion rules engine
│       ├── workspace_detector.py       # Dev workspace detection
│       └── batch_writer.py             # Batched DB inserts
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                      # Fixtures, test DB
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── services/
│   │   ├── repositories/
│   │   ├── scanner/
│   │   └── ai/
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── api/
│   │   └── workers/
│   └── fixtures/
│       ├── sample_filesystem/
│       └── mock_responses/
│
├── pyproject.toml
├── Dockerfile
└── .env.example
```

### 3.2 Frontend

```
frontend/
├── src/
│   ├── app/                             # Next.js App Router
│   │   ├── layout.tsx
│   │   ├── page.tsx                     # Dashboard overview
│   │   ├── loading.tsx
│   │   ├── error.tsx
│   │   ├── analytics/
│   │   │   └── page.tsx
│   │   ├── duplicates/
│   │   │   └── page.tsx
│   │   ├── workspaces/
│   │   │   └── page.tsx
│   │   ├── recommendations/
│   │   │   └── page.tsx
│   │   ├── cleanup/
│   │   │   └── page.tsx
│   │   ├── predictions/
│   │   │   └── page.tsx
│   │   └── settings/
│   │       └── page.tsx
│   │
│   ├── components/                      # Shared UI components
│   │   ├── ui/                          # Primitives (Button, Card, etc.)
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── data-table.tsx
│   │   │   ├── progress.tsx
│   │   │   ├── badge.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── toast.tsx
│   │   │   └── skeleton.tsx
│   │   ├── charts/
│   │   │   ├── storage-pie-chart.tsx
│   │   │   ├── growth-line-chart.tsx
│   │   │   ├── category-bar-chart.tsx
│   │   │   └── prediction-chart.tsx
│   │   ├── scan/
│   │   │   ├── scan-trigger.tsx
│   │   │   ├── scan-progress.tsx
│   │   │   └── scan-history.tsx
│   │   ├── duplicates/
│   │   │   ├── duplicate-group.tsx
│   │   │   └── duplicate-actions.tsx
│   │   ├── workspaces/
│   │   │   ├── workspace-card.tsx
│   │   │   └── workspace-detail.tsx
│   │   ├── cleanup/
│   │   │   ├── cleanup-queue.tsx
│   │   │   ├── cleanup-confirmation.tsx
│   │   │   └── audit-log.tsx
│   │   └── layout/
│   │       ├── sidebar.tsx
│   │       ├── header.tsx
│   │       └── nav-links.tsx
│   │
│   ├── hooks/                           # Custom React hooks
│   │   ├── use-scan-progress.ts         # SSE connection hook
│   │   ├── use-api.ts                   # Generic fetch wrapper
│   │   ├── use-pagination.ts
│   │   └── use-storage-stats.ts
│   │
│   ├── lib/                             # Utilities
│   │   ├── api-client.ts                # Typed API client
│   │   ├── format.ts                    # File size, date formatters
│   │   ├── constants.ts
│   │   └── types.ts                     # Shared TypeScript types
│   │
│   └── styles/
│       └── globals.css                  # Tailwind base + custom
│
├── public/
│   └── icons/
├── tests/
│   ├── components/
│   └── hooks/
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── package.json
└── Dockerfile
```

### 3.3 Infrastructure & Root

```
spaceai/
├── backend/                             # (see above)
├── frontend/                            # (see above)
├── docs/
│   ├── requirements.md
│   ├── roadmap.md
│   ├── architecture-review.md           # This document
│   ├── api-contracts.md
│   └── diagrams/
│       ├── system-architecture.mmd
│       ├── scan-flow.mmd
│       └── data-model.mmd
├── docker/
│   ├── docker-compose.yml
│   ├── docker-compose.dev.yml
│   ├── backend.Dockerfile
│   └── frontend.Dockerfile
├── scripts/
│   ├── setup.sh
│   ├── seed-db.sh
│   └── run-tests.sh
├── infrastructure/
│   └── (future: Terraform/k8s configs)
├── .kiro/
│   └── steering/
├── .github/
│   └── workflows/
│       └── ci.yml
├── .gitignore
├── Makefile
└── README.md
```

---

## 4. Database Schema

### 4.1 Entity-Relationship Overview

```
┌──────────────────┐       ┌──────────────────┐
│      scans       │───1:N─│      files       │
└──────────────────┘       └───────┬──────────┘
                                   │
                           ┌───────▼──────────┐
                           │  duplicate_groups │──1:N── duplicate_members
                           └──────────────────┘
                           
┌──────────────────┐       ┌──────────────────┐
│     folders      │       │ storage_snapshots │
└──────────────────┘       └──────────────────┘

┌──────────────────┐       ┌──────────────────┐
│ recommendations  │       │   predictions    │
└──────────────────┘       └──────────────────┘

┌──────────────────┐       ┌──────────────────┐
│  cleanup_actions │       │   audit_logs     │
└──────────────────┘       └──────────────────┘

┌──────────────────┐
│dev_workspaces    │
└──────────────────┘
```

### 4.2 Table Definitions

```sql
-- Core scan tracking
CREATE TABLE scans (
    id              TEXT PRIMARY KEY,        -- UUID
    root_path       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|running|completed|failed|cancelled
    scan_type       TEXT NOT NULL DEFAULT 'full',     -- full|incremental
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    total_files     INTEGER DEFAULT 0,
    total_dirs      INTEGER DEFAULT 0,
    total_size      INTEGER DEFAULT 0,      -- bytes
    files_per_sec   REAL,
    error_message   TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- File metadata (primary data table)
CREATE TABLE files (
    id              TEXT PRIMARY KEY,        -- UUID
    scan_id         TEXT NOT NULL REFERENCES scans(id),
    path            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    extension       TEXT,
    mime_type       TEXT,
    size            INTEGER NOT NULL,        -- bytes
    created_at      TIMESTAMP,
    modified_at     TIMESTAMP,
    accessed_at     TIMESTAMP,
    owner           TEXT,
    permissions     TEXT,
    sha256_hash     TEXT,                    -- NULL until hashed
    is_duplicate    BOOLEAN DEFAULT FALSE,
    is_stale        BOOLEAN DEFAULT FALSE,
    category        TEXT,                    -- video|image|document|archive|code|audio|other
    created_in_db   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_files_scan_id ON files(scan_id);
CREATE INDEX idx_files_path ON files(path);
CREATE INDEX idx_files_extension ON files(extension);
CREATE INDEX idx_files_size ON files(size);
CREATE INDEX idx_files_hash ON files(sha256_hash) WHERE sha256_hash IS NOT NULL;
CREATE INDEX idx_files_category ON files(category);
CREATE INDEX idx_files_modified_at ON files(modified_at);
CREATE INDEX idx_files_accessed_at ON files(accessed_at);

-- Directory aggregation
CREATE TABLE folders (
    id              TEXT PRIMARY KEY,        -- UUID
    scan_id         TEXT NOT NULL REFERENCES scans(id),
    path            TEXT NOT NULL,
    name            TEXT NOT NULL,
    total_size      INTEGER NOT NULL DEFAULT 0,
    file_count      INTEGER NOT NULL DEFAULT 0,
    depth           INTEGER NOT NULL DEFAULT 0,
    parent_path     TEXT,
    created_in_db   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_folders_scan_id ON folders(scan_id);
CREATE INDEX idx_folders_path ON folders(path);
CREATE INDEX idx_folders_total_size ON folders(total_size DESC);

-- Duplicate detection results
CREATE TABLE duplicate_groups (
    id              TEXT PRIMARY KEY,        -- UUID
    scan_id         TEXT NOT NULL REFERENCES scans(id),
    sha256_hash     TEXT NOT NULL,
    file_size       INTEGER NOT NULL,
    file_count      INTEGER NOT NULL,
    wasted_space    INTEGER NOT NULL,        -- (file_count - 1) * file_size
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_dup_groups_scan ON duplicate_groups(scan_id);
CREATE INDEX idx_dup_groups_wasted ON duplicate_groups(wasted_space DESC);

CREATE TABLE duplicate_members (
    id              TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
    file_id         TEXT NOT NULL REFERENCES files(id),
    path            TEXT NOT NULL,
    is_original     BOOLEAN DEFAULT FALSE,   -- user-designated "keep" copy
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_dup_members_group ON duplicate_members(group_id);

-- Pre-computed analytics snapshots
CREATE TABLE storage_snapshots (
    id              TEXT PRIMARY KEY,
    scan_id         TEXT REFERENCES scans(id),
    snapshot_date   DATE NOT NULL,
    total_size      INTEGER NOT NULL,
    file_count      INTEGER NOT NULL,
    dir_count       INTEGER NOT NULL,
    category_breakdown  TEXT NOT NULL,       -- JSON: {"video": bytes, "image": bytes, ...}
    top_extensions      TEXT,               -- JSON: [{"ext": ".mp4", "size": bytes, "count": n}]
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX idx_snapshots_date ON storage_snapshots(snapshot_date);

-- AI recommendations
CREATE TABLE recommendations (
    id              TEXT PRIMARY KEY,
    scan_id         TEXT REFERENCES scans(id),
    provider        TEXT NOT NULL,           -- openai|ollama
    category        TEXT NOT NULL,           -- duplicate_cleanup|stale_files|developer_cleanup|general
    priority        TEXT NOT NULL,           -- critical|high|medium|low
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    recoverable_bytes INTEGER DEFAULT 0,
    affected_paths  TEXT,                    -- JSON array of paths
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|accepted|dismissed|executed
    confidence      REAL,                   -- 0.0 - 1.0
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_recommendations_status ON recommendations(status);
CREATE INDEX idx_recommendations_priority ON recommendations(priority);

-- Predictive analytics
CREATE TABLE predictions (
    id              TEXT PRIMARY KEY,
    model_type      TEXT NOT NULL,           -- linear_regression|moving_average
    predicted_date  DATE NOT NULL,
    predicted_total INTEGER NOT NULL,        -- predicted total bytes
    growth_rate     REAL NOT NULL,           -- bytes per day
    confidence      REAL NOT NULL,           -- 0.0 - 1.0
    exhaustion_date DATE,                    -- predicted date disk is full
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Cleanup operations (safety framework)
CREATE TABLE cleanup_actions (
    id              TEXT PRIMARY KEY,
    recommendation_id TEXT REFERENCES recommendations(id),
    action_type     TEXT NOT NULL,           -- delete|trash|archive|compress
    target_paths    TEXT NOT NULL,           -- JSON array
    total_bytes     INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'proposed', -- proposed|approved|executing|completed|failed|rolled_back
    dry_run         BOOLEAN DEFAULT TRUE,
    approved_at     TIMESTAMP,
    executed_at     TIMESTAMP,
    rolled_back_at  TIMESTAMP,
    trash_location  TEXT,                    -- where trashed files went
    error_message   TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_cleanup_status ON cleanup_actions(status);

-- Full audit trail
CREATE TABLE audit_logs (
    id              TEXT PRIMARY KEY,
    action          TEXT NOT NULL,           -- scan_started|file_deleted|file_trashed|restored|recommendation_generated
    entity_type     TEXT,                    -- file|folder|scan|recommendation
    entity_id       TEXT,
    details         TEXT,                    -- JSON metadata
    bytes_affected  INTEGER DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_action ON audit_logs(action);
CREATE INDEX idx_audit_created ON audit_logs(created_at);

-- Developer workspace detection
CREATE TABLE dev_workspaces (
    id              TEXT PRIMARY KEY,
    scan_id         TEXT NOT NULL REFERENCES scans(id),
    path            TEXT NOT NULL,
    workspace_type  TEXT NOT NULL,           -- python|node|java|docker|ml|ide|cloud
    name            TEXT NOT NULL,
    total_size      INTEGER NOT NULL,
    recoverable_size INTEGER NOT NULL DEFAULT 0,
    last_modified   TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE,    -- modified within 6 months
    risk_level      TEXT DEFAULT 'low',      -- low|medium|high
    details         TEXT,                    -- JSON: specific artifacts found
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_workspaces_type ON dev_workspaces(workspace_type);
CREATE INDEX idx_workspaces_size ON dev_workspaces(total_size DESC);

-- Scan exclusion rules
CREATE TABLE exclusion_rules (
    id              TEXT PRIMARY KEY,
    pattern         TEXT NOT NULL,           -- glob pattern
    rule_type       TEXT NOT NULL,           -- path|extension|name
    reason          TEXT,
    is_system       BOOLEAN DEFAULT FALSE,   -- built-in vs user-defined
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 4.3 Design Decisions

- **UUIDs as primary keys**: Avoids sequential ID leakage, enables offline generation during batch inserts.
- **JSON columns for flexible data**: `category_breakdown`, `affected_paths`, `details` — avoids excessive normalization for metadata that's always read together.
- **Soft-delete via status**: No rows are ever physically deleted. Status transitions track lifecycle.
- **Denormalized `path` in duplicate_members**: Avoids joins for the most common query (list duplicates with paths).
- **Separate `duplicate_groups` and `duplicate_members`**: Enables efficient "show me all groups sorted by wasted space" without scanning the full files table.

---

## 5. API Contracts

### 5.1 Common Patterns

**Base URL**: `http://localhost:8000/api/v1`

**Pagination** (query params on all list endpoints):
```
?page=1&page_size=50&sort_by=size&sort_order=desc
```

**Standard Response Envelope**:
```json
{
  "data": [...],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total_items": 1234,
    "total_pages": 25
  }
}
```

**Error Response**:
```json
{
  "error": {
    "code": "SCAN_NOT_FOUND",
    "message": "Scan with id 'abc-123' does not exist",
    "details": {}
  }
}
```

### 5.2 Scan Endpoints

```
POST   /api/v1/scans
GET    /api/v1/scans
GET    /api/v1/scans/{scan_id}
DELETE /api/v1/scans/{scan_id}          (cancel running scan)
GET    /api/v1/scans/{scan_id}/progress (SSE stream)
```

**POST /api/v1/scans** — Start a new scan
```json
// Request
{
  "root_path": "/Users/vamsig",
  "scan_type": "full",                  // full | incremental
  "exclusions": ["node_modules", ".git"],
  "max_depth": null                     // null = unlimited
}

// Response 202 Accepted
{
  "data": {
    "id": "uuid-here",
    "status": "pending",
    "root_path": "/Users/vamsig",
    "scan_type": "full",
    "created_at": "2026-06-23T10:00:00Z"
  }
}
```

**GET /api/v1/scans/{scan_id}/progress** — SSE Stream
```
event: progress
data: {"files_scanned": 45000, "dirs_scanned": 1200, "current_path": "/Users/vamsig/projects", "bytes_total": 53687091200, "eta_seconds": 120}

event: completed
data: {"total_files": 892341, "total_size": 214748364800, "duration_seconds": 87}
```

### 5.3 File Endpoints

```
GET    /api/v1/files?scan_id=&category=&min_size=&max_size=&extension=
GET    /api/v1/files/{file_id}
GET    /api/v1/files/largest?limit=100
GET    /api/v1/files/stale?days_unused=180
```

**GET /api/v1/files** — List files with filters
```json
// Query params: scan_id, category, min_size, max_size, extension, is_stale, is_duplicate
// Response 200
{
  "data": [
    {
      "id": "uuid",
      "path": "/Users/vamsig/Downloads/model.ckpt",
      "filename": "model.ckpt",
      "extension": ".ckpt",
      "size": 5368709120,
      "category": "other",
      "modified_at": "2024-01-15T08:30:00Z",
      "accessed_at": "2024-01-15T08:30:00Z",
      "is_duplicate": false,
      "is_stale": true
    }
  ],
  "meta": { "page": 1, "page_size": 50, "total_items": 892341, "total_pages": 17847 }
}
```

### 5.4 Folder Endpoints

```
GET    /api/v1/folders?scan_id=&min_size=&parent_path=
GET    /api/v1/folders/{folder_id}
GET    /api/v1/folders/largest?limit=50
GET    /api/v1/folders/tree?root_path=&depth=3
```

### 5.5 Duplicate Endpoints

```
GET    /api/v1/duplicates?scan_id=&min_wasted=
GET    /api/v1/duplicates/{group_id}
POST   /api/v1/duplicates/{group_id}/resolve
GET    /api/v1/duplicates/summary
```

**GET /api/v1/duplicates/summary**
```json
{
  "data": {
    "total_groups": 456,
    "total_duplicate_files": 1893,
    "total_wasted_bytes": 15032385536,
    "top_extensions": [".jpg", ".pdf", ".mp4"]
  }
}
```

**POST /api/v1/duplicates/{group_id}/resolve** — Mark which to keep
```json
// Request
{
  "keep_file_id": "uuid-of-original",
  "action": "trash"                     // trash | delete | archive
}

// Response 200
{
  "data": {
    "cleanup_action_id": "uuid",
    "status": "proposed",
    "files_affected": 3,
    "bytes_recoverable": 15728640
  }
}
```

### 5.6 Analytics Endpoints

```
GET    /api/v1/analytics/overview
GET    /api/v1/analytics/categories?scan_id=
GET    /api/v1/analytics/growth?period=30d
GET    /api/v1/analytics/history?from=&to=
```

**GET /api/v1/analytics/overview**
```json
{
  "data": {
    "total_storage": 500107862016,
    "used_storage": 387028092928,
    "free_storage": 113079769088,
    "file_count": 892341,
    "duplicate_waste": 15032385536,
    "stale_files_size": 42949672960,
    "recovery_opportunities": 58982058496,
    "last_scan": "2026-06-23T10:00:00Z"
  }
}
```

### 5.7 Recommendation Endpoints

```
POST   /api/v1/recommendations/generate
GET    /api/v1/recommendations?status=&priority=&category=
GET    /api/v1/recommendations/{rec_id}
PATCH  /api/v1/recommendations/{rec_id}  (accept/dismiss)
```

**POST /api/v1/recommendations/generate**
```json
// Request
{
  "scan_id": "uuid",
  "provider": "openai",                 // openai | ollama
  "categories": ["all"]                 // or specific: ["developer_cleanup", "duplicates"]
}

// Response 202 Accepted
{
  "data": {
    "task_id": "uuid",
    "status": "generating"
  }
}
```

**GET /api/v1/recommendations**
```json
{
  "data": [
    {
      "id": "uuid",
      "category": "developer_cleanup",
      "priority": "high",
      "title": "Remove unused Docker volumes",
      "description": "Found 8 dangling Docker volumes consuming 14GB...",
      "recoverable_bytes": 15032385536,
      "confidence": 0.92,
      "status": "pending",
      "affected_paths": ["/var/lib/docker/volumes/..."],
      "created_at": "2026-06-23T10:05:00Z"
    }
  ],
  "meta": { ... }
}
```

### 5.8 Prediction Endpoints

```
GET    /api/v1/predictions/forecast?days=90
GET    /api/v1/predictions/exhaustion
GET    /api/v1/predictions/growth-rate
```

**GET /api/v1/predictions/exhaustion**
```json
{
  "data": {
    "current_free_bytes": 113079769088,
    "daily_growth_bytes": 351272960,
    "weekly_growth_bytes": 2458910720,
    "exhaustion_date": "2026-10-14",
    "days_remaining": 113,
    "confidence": 0.78,
    "model_type": "linear_regression"
  }
}
```

### 5.9 Cleanup Endpoints

```
POST   /api/v1/cleanup/execute
GET    /api/v1/cleanup/actions?status=
GET    /api/v1/cleanup/actions/{action_id}
POST   /api/v1/cleanup/actions/{action_id}/approve
POST   /api/v1/cleanup/actions/{action_id}/rollback
GET    /api/v1/cleanup/audit-log
```

**POST /api/v1/cleanup/execute**
```json
// Request
{
  "action_id": "uuid",
  "dry_run": false
}

// Response 200
{
  "data": {
    "action_id": "uuid",
    "status": "completed",
    "files_processed": 12,
    "bytes_recovered": 5368709120,
    "trash_location": "~/.spaceai/trash/2026-06-23/",
    "rollback_available": true
  }
}
```

### 5.10 Developer Workspace Endpoints

```
GET    /api/v1/workspaces?scan_id=&type=&min_size=
GET    /api/v1/workspaces/{workspace_id}
GET    /api/v1/workspaces/summary
```

```
GET    /api/v1/developer-analysis/overview
GET    /api/v1/developer-analysis/abandoned-projects
GET    /api/v1/developer-analysis/duplicate-projects
GET    /api/v1/developer-analysis/download-bloat
GET    /api/v1/developer-analysis/model-hoarding
```

### 5.11 Health & System

```
GET    /api/v1/health
GET    /api/v1/health/ready
GET    /api/v1/metrics
```

---

## 6. Implementation Plan

### Phase 1: Foundation & Scanner (Weeks 1-2)

| Task | Priority | Complexity |
|------|----------|-----------|
| Project scaffolding (pyproject.toml, next.config, docker-compose) | P0 | Low |
| Database setup: SQLAlchemy models, Alembic, session factory | P0 | Medium |
| Base repository with CRUD operations | P0 | Medium |
| Config management (pydantic-settings) | P0 | Low |
| Filesystem crawler with multi-threaded traversal | P0 | High |
| Batch writer (1000 records/insert) | P0 | Medium |
| Exclusion rules engine | P0 | Medium |
| Task manager for background scans | P0 | High |
| SSE endpoint for scan progress | P0 | Medium |
| Scan API routes (POST, GET, cancel) | P0 | Medium |
| Unit tests for scanner, batch writer, exclusion rules | P0 | Medium |

**Exit Criteria**: Can scan a directory of 100K+ files, store metadata in SQLite, stream progress via SSE.

### Phase 2: Analytics (Week 3)

| Task | Priority | Complexity |
|------|----------|-----------|
| File categorization service (extension → category mapping) | P0 | Low |
| Largest files/folders query optimizations | P0 | Medium |
| Storage snapshot generation (post-scan hook) | P0 | Medium |
| Category breakdown computation | P0 | Low |
| Historical growth tracking | P0 | Medium |
| Analytics API endpoints | P0 | Medium |
| Frontend: Overview dashboard page | P1 | Medium |
| Frontend: Charts (Recharts integration) | P1 | Medium |

**Exit Criteria**: Dashboard shows storage breakdown, top files/folders, historical trend chart.

### Phase 3: Duplicate Detection (Week 4)

| Task | Priority | Complexity |
|------|----------|-----------|
| Size-grouping pass (find same-size files) | P0 | Medium |
| Streaming SHA256 hasher (64KB buffer) | P0 | Medium |
| Hash worker (multi-threaded, batched) | P0 | High |
| Duplicate group creation | P0 | Medium |
| Duplicate resolution API | P0 | Medium |
| Frontend: Duplicate management page | P1 | Medium |

**Exit Criteria**: Detects duplicates across 100K+ files in < 5 minutes (SSD). UI shows groups with resolve actions.

### Phase 4: Stale File Analysis (Week 5)

| Task | Priority | Complexity |
|------|----------|-----------|
| Stale file identification service (configurable thresholds) | P0 | Medium |
| Risk scoring algorithm | P0 | Medium |
| Confidence scoring | P0 | Medium |
| Stale file API endpoints | P0 | Low |
| Integration with analytics | P0 | Low |

**Exit Criteria**: Files not accessed in N days are flagged with risk/confidence scores.

### Phase 5: Developer Workspace Detection (Weeks 6-7)

| Task | Priority | Complexity |
|------|----------|-----------|
| Workspace detector (pattern matching per language ecosystem) | P0 | High |
| Python workspace analyzer (.venv, __pycache__, pip cache) | P0 | Medium |
| Node workspace analyzer (node_modules, caches) | P0 | Medium |
| Java workspace analyzer (target, .gradle, build) | P0 | Medium |
| Docker analyzer (requires Docker SDK) | P0 | High |
| ML model detector (.pt, .ckpt, .onnx, HF cache) | P0 | Medium |
| IDE artifact detector | P0 | Low |
| Recoverable space estimation | P0 | Medium |
| Workspace API endpoints | P0 | Medium |
| Frontend: Workspace dashboard | P1 | High |

**Exit Criteria**: Scans detect all workspace types, estimate recovery, categorize by risk.

### Phase 6: Smart Developer Analysis (Week 8)

| Task | Priority | Complexity |
|------|----------|-----------|
| Abandoned project detection (activity heuristics) | P0 | High |
| Duplicate project detection (name similarity + content overlap) | P0 | High |
| Old coursework detection | P1 | Medium |
| Download bloat analysis | P0 | Medium |
| Model hoarding analysis | P0 | Medium |
| Developer analysis API endpoints | P0 | Medium |
| Frontend: Developer analysis page | P1 | Medium |

**Exit Criteria**: Generates actionable intelligence about project lifecycle and waste patterns.

### Phase 7: AI Recommendation Engine (Week 9)

| Task | Priority | Complexity |
|------|----------|-----------|
| Provider abstraction layer (base interface) | P0 | Medium |
| OpenAI provider implementation | P0 | Medium |
| Ollama provider implementation | P0 | Medium |
| Provider factory with config-driven selection | P0 | Low |
| Prompt engineering (recommendation, summary, analysis) | P0 | High |
| Circuit breaker wrapper | P0 | Medium |
| Recommendation generation service | P0 | High |
| Recommendation API endpoints | P0 | Medium |
| Frontend: Recommendations page | P1 | Medium |

**Exit Criteria**: AI generates prioritized, actionable recommendations. Graceful fallback when providers fail.

### Phase 8: Predictive Analytics (Week 10)

| Task | Priority | Complexity |
|------|----------|-----------|
| Linear regression model (scikit-learn) | P0 | Medium |
| Moving average computation | P0 | Low |
| Exhaustion date calculation | P0 | Medium |
| Confidence interval calculation | P0 | Medium |
| Prediction API endpoints | P0 | Low |
| Frontend: Prediction chart | P1 | Medium |

**Exit Criteria**: Predicts disk exhaustion date with confidence levels. Requires 7+ snapshots of history.

### Phase 9: Safety Framework (Week 11)

| Task | Priority | Complexity |
|------|----------|-----------|
| Trash manager (move to ~/.spaceai/trash/) | P0 | High |
| Dry-run executor (simulates without action) | P0 | Medium |
| Approval workflow (propose → approve → execute) | P0 | High |
| Rollback service (restore from trash) | P0 | High |
| Audit logging service | P0 | Medium |
| Cleanup API endpoints | P0 | Medium |
| Frontend: Cleanup center with confirmation dialogs | P0 | High |

**Exit Criteria**: Full safety lifecycle works. Files can be trashed and restored. Audit trail is complete.

### Phase 10: API Hardening (Week 12)

| Task | Priority | Complexity |
|------|----------|-----------|
| Input validation (path canonicalization, size limits) | P0 | Medium |
| Error handling middleware | P0 | Medium |
| Request logging middleware | P0 | Low |
| OpenAPI documentation polish | P1 | Low |
| API versioning setup | P1 | Low |
| Rate limiting (optional) | P2 | Low |

**Exit Criteria**: All endpoints handle edge cases gracefully. OpenAPI spec is complete and accurate.

### Phase 11: Frontend Polish (Week 13)

| Task | Priority | Complexity |
|------|----------|-----------|
| Responsive layout (mobile-friendly sidebar) | P1 | Medium |
| Virtual scrolling for large lists | P1 | Medium |
| Loading states (skeletons) | P1 | Low |
| Error boundaries | P1 | Low |
| Toast notifications | P1 | Low |
| Dark mode | P2 | Low |
| Keyboard navigation / a11y | P1 | Medium |

### Phase 12: Observability (Week 14)

| Task | Priority | Complexity |
|------|----------|-----------|
| Structured logging (structlog) | P0 | Medium |
| Scan telemetry (duration, throughput) | P0 | Low |
| API latency tracking | P1 | Low |
| Error tracking and alerting hooks | P1 | Medium |
| Health check endpoints | P0 | Low |

### Phase 13: Testing & Coverage (Ongoing, finalize Week 15)

| Task | Priority | Complexity |
|------|----------|-----------|
| Backend unit tests (services, repositories, scanner) | P0 | High |
| Backend integration tests (API routes with test DB) | P0 | High |
| Frontend component tests (React Testing Library) | P1 | Medium |
| Coverage reporting (80%+ target) | P0 | Low |
| CI pipeline (GitHub Actions) | P1 | Medium |

### Phase 14: Documentation & Docker (Week 16)

| Task | Priority | Complexity |
|------|----------|-----------|
| README with setup guide | P0 | Medium |
| Architecture diagrams (Mermaid) | P1 | Medium |
| Docker Compose for full stack | P0 | Medium |
| Development environment setup script | P1 | Low |
| API documentation (auto-generated from OpenAPI) | P0 | Low |

---

## 7. Dependencies & Libraries

### 7.1 Backend (Python 3.12+)

```toml
[project]
dependencies = [
    # Web Framework
    "fastapi==0.115.*",
    "uvicorn[standard]==0.32.*",
    "pydantic==2.11.*",
    "pydantic-settings==2.7.*",
    
    # Database
    "sqlalchemy==2.0.*",
    "aiosqlite==0.20.*",          # Async SQLite driver
    "alembic==1.14.*",
    
    # Data Processing
    "pandas==2.2.*",
    "numpy==2.1.*",
    "scikit-learn==1.6.*",
    
    # AI Providers
    "openai==1.82.*",
    "httpx==0.28.*",              # For Ollama HTTP calls
    
    # Utilities
    "python-multipart==0.0.*",    # File uploads
    "structlog==24.4.*",          # Structured logging
    "python-magic==0.4.*",        # MIME type detection
    "click==8.1.*",               # CLI commands
    
    # Async
    "anyio==4.7.*",
]

[project.optional-dependencies]
dev = [
    "pytest==8.3.*",
    "pytest-asyncio==0.24.*",
    "pytest-cov==6.0.*",
    "httpx==0.28.*",              # TestClient
    "factory-boy==3.3.*",         # Test factories
    "ruff==0.8.*",                # Linter + formatter
    "mypy==1.13.*",               # Type checking
]

docker = [
    "docker==7.1.*",              # Docker SDK for Python
]
```

### 7.2 Frontend (Node 20+)

```json
{
  "dependencies": {
    "next": "^15.1",
    "react": "^19.0",
    "react-dom": "^19.0",
    "typescript": "^5.7",
    "tailwindcss": "^4.0",
    "recharts": "^2.15",
    "@tanstack/react-query": "^5.62",
    "lucide-react": "^0.468",
    "clsx": "^2.1",
    "tailwind-merge": "^2.6",
    "date-fns": "^4.1",
    "zod": "^3.24"
  },
  "devDependencies": {
    "@testing-library/react": "^16.1",
    "@testing-library/jest-dom": "^6.6",
    "vitest": "^2.1",
    "@vitejs/plugin-react": "^4.3",
    "eslint": "^9.16",
    "eslint-config-next": "^15.1",
    "prettier": "^3.4",
    "prettier-plugin-tailwindcss": "^0.6"
  }
}
```

### 7.3 Infrastructure

```yaml
# docker-compose.yml services needed
services:
  backend:   # Python/FastAPI
  frontend:  # Next.js
  # Future:
  # redis:   # When task queue is needed
  # postgres: # When scaling beyond SQLite
```

### 7.4 Key Library Decisions

| Need | Choice | Rationale |
|------|--------|-----------|
| HTTP client (AI calls) | `httpx` | Async-native, used by FastAPI TestClient too |
| MIME detection | `python-magic` | Uses libmagic, more accurate than extension-only |
| Logging | `structlog` | JSON-structured logs, context binding, fast |
| Frontend data fetching | `@tanstack/react-query` | Caching, refetching, SSE support, mutation hooks |
| Frontend charts | `recharts` | Specified in requirements, React-native, composable |
| Frontend validation | `zod` | Runtime type validation for API responses |
| Testing (FE) | `vitest` | Faster than Jest, native ESM, compatible with Testing Library |
| Type checking | `mypy` | Enforces the "type hints everywhere" standard |
| Linting | `ruff` | Replaces flake8+isort+black, extremely fast |
| DB migrations | `alembic` | Only real choice for SQLAlchemy migrations |
| Docker SDK | `docker` | Official Python SDK, needed for Phase 5 Docker analysis |

---

## Summary of Critical Recommendations

1. **Add a background task system from day one.** Without it, scans will timeout on large filesystems.
2. **Use WAL mode for SQLite** and batch writes to avoid lock contention.
3. **Design the repository layer as DB-agnostic** — SQLAlchemy's unit of work pattern makes this natural if done correctly.
4. **Alembic is non-negotiable** for a 14-phase project with evolving schema.
5. **SSE for progress** — the scanner must report progress without polling.
6. **Circuit breaker on AI providers** — LLM calls are unreliable and slow.
7. **Pre-compute analytics** on scan completion rather than querying raw tables per request.
8. **Safety framework is architectural, not just a feature.** Every destructive path in the system must route through the approval workflow. Design it as middleware, not bolted-on.

The architecture is ambitious but well-scoped. The phased approach is correct — each phase builds on the previous one's data layer. The main risk is Phase 5-6 (developer workspace analysis) which requires deep platform-specific knowledge and may need platform abstraction (macOS vs Linux paths, Docker socket locations, etc.).

Ready to begin Phase 1 implementation on your signal.

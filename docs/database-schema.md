# Database Schema & Migration Strategy

**Reference:** ADR-002 (SQLite with WAL Mode)  
**Date:** 2026-06-23

---

## 1. Migration Strategy (Alembic)

### Setup

```
backend/
├── alembic/
│   ├── versions/           # Auto-generated migration files
│   │   ├── 001_initial_schema.py
│   │   ├── 002_add_duplicate_tables.py
│   │   ├── 003_add_workspace_tables.py
│   │   └── ...
│   ├── env.py              # Alembic environment config
│   └── script.py.mako      # Migration template
├── alembic.ini             # Alembic configuration
```

### Migration Workflow

1. **Modify SQLAlchemy model** in `app/models/`.
2. **Auto-generate migration**: `alembic revision --autogenerate -m "description"`
3. **Review generated migration** (always inspect — autogenerate isn't perfect).
4. **Apply migration**: `alembic upgrade head`
5. **Rollback if needed**: `alembic downgrade -1`

### Migration Rules

- Every migration must be reversible (implement both `upgrade()` and `downgrade()`).
- Never modify an existing migration after it's been committed to main.
- Data migrations (backfills) are separate from schema migrations.
- Each phase gets its own set of migrations (numbered by phase prefix).
- Test migrations against a fresh DB and against a DB with existing data.

### Phase-to-Migration Mapping

| Phase | Migration | Tables Affected |
|-------|-----------|-----------------|
| 1 | 001_initial_schema | scans, files, folders, exclusion_rules |
| 2 | 002_add_snapshots | storage_snapshots |
| 3 | 003_add_duplicates | duplicate_groups, duplicate_members |
| 4 | 004_add_stale_fields | files (add is_stale, stale_score columns) |
| 5 | 005_add_workspaces | dev_workspaces |
| 7 | 006_add_recommendations | recommendations |
| 8 | 007_add_predictions | predictions |
| 9 | 008_add_cleanup | cleanup_actions, audit_logs |

### SQLite-Specific Migration Concerns

SQLite has limited `ALTER TABLE` support:
- Can ADD columns.
- Cannot DROP columns (before SQLite 3.35.0), cannot RENAME columns (before 3.25.0).
- Cannot modify column types or constraints.

Alembic handles this via **batch mode** (`render_as_batch=True` in env.py):
```python
# alembic/env.py
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=True,  # Required for SQLite ALTER TABLE support
)
```

Batch mode creates a temp table, copies data, drops original, renames temp. This is safe but requires downtime for large tables. For the `files` table (potentially millions of rows), plan migration windows.

---

## 2. Complete Schema Definition

### 2.1 Core Tables (Phase 1)

```sql
-- ============================================================
-- SCANS: Tracks all filesystem scan operations
-- ============================================================
CREATE TABLE scans (
    id                  TEXT PRIMARY KEY,          -- UUIDv4
    root_path           TEXT NOT NULL,             -- Absolute path scanned
    status              TEXT NOT NULL DEFAULT 'pending',
                        -- pending | running | completed | failed | cancelled
    scan_type           TEXT NOT NULL DEFAULT 'full',
                        -- full | incremental
    started_at          TEXT,                      -- ISO8601 timestamp
    completed_at        TEXT,                      -- ISO8601 timestamp
    total_files         INTEGER NOT NULL DEFAULT 0,
    total_dirs          INTEGER NOT NULL DEFAULT 0,
    total_size_bytes    INTEGER NOT NULL DEFAULT 0,
    files_per_second    REAL,
    error_message       TEXT,
    checkpoint_data     TEXT,                      -- JSON: {"last_directory": "...", "files_so_far": N}
    exclusion_patterns  TEXT,                      -- JSON array: ["node_modules", ".git"]
    platform            TEXT,                      -- windows | macos | linux
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_scans_status ON scans(status);
CREATE INDEX idx_scans_created_at ON scans(created_at);


-- ============================================================
-- FILES: All discovered file metadata
-- ============================================================
CREATE TABLE files (
    id                  TEXT PRIMARY KEY,          -- UUIDv4
    scan_id             TEXT NOT NULL,             -- FK → scans.id
    path                TEXT NOT NULL,             -- Full absolute path (normalized with /)
    directory           TEXT NOT NULL,             -- Parent directory path
    filename            TEXT NOT NULL,             -- Basename
    extension           TEXT,                      -- Lowercase, with dot: .py, .js
    size_bytes          INTEGER NOT NULL,          -- File size in bytes
    mime_type           TEXT,                      -- NULL until enriched
    category            TEXT,                      -- video|image|document|archive|code|audio|data|other
    created_at          TEXT,                      -- File creation time (ISO8601)
    modified_at         TEXT,                      -- File modification time (ISO8601)
    accessed_at         TEXT,                      -- File access time (ISO8601)
    owner               TEXT,                      -- POSIX owner (NULL on Windows)
    permissions         TEXT,                      -- Octal string: "755" (NULL on Windows)
    sha256_hash         TEXT,                      -- NULL until hash pass runs
    is_duplicate        INTEGER NOT NULL DEFAULT 0,  -- SQLite boolean
    is_stale            INTEGER NOT NULL DEFAULT 0,  -- SQLite boolean
    stale_score         REAL,                      -- 0.0 - 1.0 (NULL until scored)
    risk_level          TEXT,                      -- low|medium|high (NULL until scored)
    discovered_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);

-- Performance-critical indexes for NFR: <200ms queries
CREATE INDEX idx_files_scan_id ON files(scan_id);
CREATE INDEX idx_files_directory ON files(directory);
CREATE INDEX idx_files_extension ON files(extension);
CREATE INDEX idx_files_size_bytes ON files(size_bytes DESC);
CREATE INDEX idx_files_category ON files(category);
CREATE INDEX idx_files_modified_at ON files(modified_at);
CREATE INDEX idx_files_accessed_at ON files(accessed_at);
CREATE INDEX idx_files_hash ON files(sha256_hash) WHERE sha256_hash IS NOT NULL;
CREATE INDEX idx_files_is_stale ON files(is_stale) WHERE is_stale = 1;
CREATE INDEX idx_files_is_duplicate ON files(is_duplicate) WHERE is_duplicate = 1;

-- Composite index for size-based duplicate candidate grouping
CREATE INDEX idx_files_size_scan ON files(scan_id, size_bytes);


-- ============================================================
-- FOLDERS: Aggregated directory metadata
-- ============================================================
CREATE TABLE folders (
    id                  TEXT PRIMARY KEY,          -- UUIDv4
    scan_id             TEXT NOT NULL,             -- FK → scans.id
    path                TEXT NOT NULL,             -- Full absolute path
    name                TEXT NOT NULL,             -- Directory basename
    parent_path         TEXT,                      -- Parent directory (NULL for root)
    depth               INTEGER NOT NULL DEFAULT 0,
    total_size_bytes    INTEGER NOT NULL DEFAULT 0, -- Sum of all contained files (recursive)
    file_count          INTEGER NOT NULL DEFAULT 0, -- Direct + nested file count
    dir_count           INTEGER NOT NULL DEFAULT 0, -- Direct child directory count
    discovered_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);

CREATE INDEX idx_folders_scan_id ON folders(scan_id);
CREATE INDEX idx_folders_path ON folders(path);
CREATE INDEX idx_folders_parent ON folders(parent_path);
CREATE INDEX idx_folders_size ON folders(total_size_bytes DESC);


-- ============================================================
-- EXCLUSION_RULES: Configurable scan exclusion patterns
-- ============================================================
CREATE TABLE exclusion_rules (
    id                  TEXT PRIMARY KEY,
    pattern             TEXT NOT NULL,             -- Glob pattern: "*.pyc", "node_modules"
    rule_type           TEXT NOT NULL,             -- path | extension | name | regex
    description         TEXT,
    is_system           INTEGER NOT NULL DEFAULT 0, -- Built-in vs user-defined
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

### 2.2 Analytics Tables (Phase 2)

```sql
-- ============================================================
-- STORAGE_SNAPSHOTS: Pre-computed analytics per scan
-- ============================================================
CREATE TABLE storage_snapshots (
    id                  TEXT PRIMARY KEY,
    scan_id             TEXT NOT NULL,             -- FK → scans.id
    snapshot_date       TEXT NOT NULL,             -- DATE: YYYY-MM-DD
    total_size_bytes    INTEGER NOT NULL,
    used_size_bytes     INTEGER NOT NULL,
    file_count          INTEGER NOT NULL,
    dir_count           INTEGER NOT NULL,
    category_breakdown  TEXT NOT NULL,             -- JSON: {"video": 1234, "image": 5678, ...}
    extension_breakdown TEXT,                      -- JSON: [{"ext": ".mp4", "size": N, "count": N}]
    largest_files       TEXT,                      -- JSON: top 20 files [{path, size}]
    largest_dirs        TEXT,                      -- JSON: top 20 dirs [{path, size}]
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX idx_snapshots_date ON storage_snapshots(snapshot_date);
CREATE INDEX idx_snapshots_scan ON storage_snapshots(scan_id);
```

### 2.3 Duplicate Tables (Phase 3)

```sql
-- ============================================================
-- DUPLICATE_GROUPS: Groups of files sharing the same hash
-- ============================================================
CREATE TABLE duplicate_groups (
    id                  TEXT PRIMARY KEY,
    scan_id             TEXT NOT NULL,
    sha256_hash         TEXT NOT NULL,
    file_size_bytes     INTEGER NOT NULL,         -- Size of each file in the group
    member_count        INTEGER NOT NULL,         -- Number of duplicate files
    wasted_bytes        INTEGER NOT NULL,         -- (member_count - 1) * file_size_bytes
    status              TEXT NOT NULL DEFAULT 'unresolved',
                        -- unresolved | resolved | partially_resolved
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);

CREATE INDEX idx_dup_groups_scan ON duplicate_groups(scan_id);
CREATE INDEX idx_dup_groups_wasted ON duplicate_groups(wasted_bytes DESC);
CREATE INDEX idx_dup_groups_hash ON duplicate_groups(sha256_hash);


-- ============================================================
-- DUPLICATE_MEMBERS: Individual files within a duplicate group
-- ============================================================
CREATE TABLE duplicate_members (
    id                  TEXT PRIMARY KEY,
    group_id            TEXT NOT NULL,             -- FK → duplicate_groups.id
    file_id             TEXT NOT NULL,             -- FK → files.id
    path                TEXT NOT NULL,             -- Denormalized for query speed
    is_keeper           INTEGER NOT NULL DEFAULT 0, -- User-designated "keep" copy
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (group_id) REFERENCES duplicate_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX idx_dup_members_group ON duplicate_members(group_id);
CREATE INDEX idx_dup_members_file ON duplicate_members(file_id);
```

### 2.4 Developer Workspace Tables (Phase 5-6)

```sql
-- ============================================================
-- DEV_WORKSPACES: Detected developer project workspaces
-- ============================================================
CREATE TABLE dev_workspaces (
    id                  TEXT PRIMARY KEY,
    scan_id             TEXT NOT NULL,
    path                TEXT NOT NULL,             -- Root of the workspace
    name                TEXT NOT NULL,             -- Project/directory name
    workspace_type      TEXT NOT NULL,             -- python|node|java|rust|go|docker|ml|ide|cloud
    total_size_bytes    INTEGER NOT NULL,
    recoverable_bytes   INTEGER NOT NULL DEFAULT 0,
    safe_recoverable_bytes INTEGER NOT NULL DEFAULT 0,  -- Low-risk subset
    last_modified_at    TEXT,                      -- Most recent file modification in workspace
    is_active           INTEGER NOT NULL DEFAULT 1, -- Modified within threshold (default: 6 months)
    days_inactive       INTEGER,                   -- Days since last modification
    risk_level          TEXT NOT NULL DEFAULT 'low', -- low|medium|high
    artifacts           TEXT NOT NULL,             -- JSON: [{"type": "node_modules", "size": N, "path": "..."}]
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);

CREATE INDEX idx_workspaces_scan ON dev_workspaces(scan_id);
CREATE INDEX idx_workspaces_type ON dev_workspaces(workspace_type);
CREATE INDEX idx_workspaces_size ON dev_workspaces(total_size_bytes DESC);
CREATE INDEX idx_workspaces_inactive ON dev_workspaces(is_active, days_inactive);
```

### 2.5 AI & Recommendation Tables (Phase 7)

```sql
-- ============================================================
-- RECOMMENDATIONS: AI-generated storage optimization advice
-- ============================================================
CREATE TABLE recommendations (
    id                  TEXT PRIMARY KEY,
    scan_id             TEXT,                      -- FK → scans.id (NULL for manual)
    provider            TEXT NOT NULL,             -- openai | ollama
    model               TEXT,                      -- gpt-4o, llama3, etc.
    category            TEXT NOT NULL,
                        -- duplicate_cleanup | stale_files | developer_cleanup | 
                        -- workspace_archival | download_bloat | model_hoarding | general
    priority            TEXT NOT NULL,             -- critical | high | medium | low
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    explanation         TEXT,                      -- Detailed reasoning from AI
    recoverable_bytes   INTEGER NOT NULL DEFAULT 0,
    confidence          REAL NOT NULL DEFAULT 0.0, -- 0.0 - 1.0
    affected_paths      TEXT,                      -- JSON array of paths
    affected_count      INTEGER NOT NULL DEFAULT 0, -- Number of files/items affected
    status              TEXT NOT NULL DEFAULT 'pending',
                        -- pending | accepted | dismissed | executed | expired
    dismissed_reason    TEXT,                      -- User reason for dismissal
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE SET NULL
);

CREATE INDEX idx_rec_status ON recommendations(status);
CREATE INDEX idx_rec_priority ON recommendations(priority);
CREATE INDEX idx_rec_category ON recommendations(category);
CREATE INDEX idx_rec_scan ON recommendations(scan_id);
```

### 2.6 Prediction Tables (Phase 8)

```sql
-- ============================================================
-- PREDICTIONS: Storage growth forecasting results
-- ============================================================
CREATE TABLE predictions (
    id                  TEXT PRIMARY KEY,
    model_type          TEXT NOT NULL,             -- linear_regression | moving_average | exponential
    input_snapshots     INTEGER NOT NULL,          -- Number of data points used
    daily_growth_bytes  REAL NOT NULL,             -- Predicted bytes per day
    weekly_growth_bytes REAL NOT NULL,
    predicted_total_30d INTEGER,                   -- Predicted total in 30 days
    predicted_total_90d INTEGER,                   -- Predicted total in 90 days
    exhaustion_date     TEXT,                      -- Predicted date disk is full (YYYY-MM-DD)
    days_until_full     INTEGER,
    confidence          REAL NOT NULL,             -- 0.0 - 1.0
    confidence_interval TEXT,                      -- JSON: {"lower": N, "upper": N}
    metadata            TEXT,                      -- JSON: model coefficients, R² score
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_predictions_created ON predictions(created_at DESC);
```

### 2.7 Safety & Audit Tables (Phase 9)

```sql
-- ============================================================
-- CLEANUP_ACTIONS: Proposed, approved, and executed cleanup operations
-- ============================================================
CREATE TABLE cleanup_actions (
    id                  TEXT PRIMARY KEY,
    recommendation_id   TEXT,                      -- FK → recommendations.id (NULL for manual)
    action_type         TEXT NOT NULL,             -- trash | archive | compress | delete_permanent
    target_paths        TEXT NOT NULL,             -- JSON array of absolute paths
    target_count        INTEGER NOT NULL,          -- Number of files affected
    total_bytes         INTEGER NOT NULL,          -- Total size of targets
    status              TEXT NOT NULL DEFAULT 'proposed',
                        -- proposed | approved | dry_run_complete | executing | 
                        -- completed | failed | rolled_back
    dry_run_result      TEXT,                      -- JSON: simulated result
    approved_at         TEXT,
    approved_by         TEXT,                      -- Future: user ID
    executed_at         TEXT,
    completed_at        TEXT,
    rolled_back_at      TEXT,
    trash_location      TEXT,                      -- Path where trashed files stored
    manifest_path       TEXT,                      -- Path to restore manifest JSON
    error_message       TEXT,
    bytes_recovered     INTEGER DEFAULT 0,         -- Actual bytes freed
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    
    FOREIGN KEY (recommendation_id) REFERENCES recommendations(id) ON DELETE SET NULL
);

CREATE INDEX idx_cleanup_status ON cleanup_actions(status);
CREATE INDEX idx_cleanup_created ON cleanup_actions(created_at DESC);


-- ============================================================
-- AUDIT_LOGS: Immutable record of all system actions
-- ============================================================
CREATE TABLE audit_logs (
    id                  TEXT PRIMARY KEY,
    correlation_id      TEXT,                      -- Links related events
    action              TEXT NOT NULL,
                        -- scan_started | scan_completed | scan_failed |
                        -- file_trashed | file_restored | file_deleted |
                        -- recommendation_generated | recommendation_accepted |
                        -- recommendation_dismissed | cleanup_executed |
                        -- cleanup_rolled_back | settings_changed
    entity_type         TEXT,                      -- scan | file | folder | recommendation | cleanup_action
    entity_id           TEXT,                      -- ID of affected entity
    description         TEXT,                      -- Human-readable description
    metadata            TEXT,                      -- JSON: action-specific details
    bytes_affected      INTEGER DEFAULT 0,
    paths_affected      TEXT,                      -- JSON array (subset, for display)
    severity            TEXT NOT NULL DEFAULT 'info', -- info | warning | critical
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_audit_action ON audit_logs(action);
CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX idx_audit_correlation ON audit_logs(correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX idx_audit_severity ON audit_logs(severity) WHERE severity != 'info';
```

---

## 3. Schema Design Decisions

### 3.1 Why TEXT for Timestamps (not TIMESTAMP)

SQLite has no native TIMESTAMP type. All date/time values are stored as ISO8601 TEXT strings (`YYYY-MM-DDTHH:MM:SS.fffZ`). This:
- Enables lexicographic sorting (ISO8601 sorts correctly as text).
- Is human-readable in DB inspection tools.
- Avoids ambiguity with SQLite's type affinity system.
- Uses `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` for defaults.

### 3.2 Why TEXT for UUIDs (not BLOB)

- Readable in debug queries.
- Compatible with API responses (no conversion needed).
- Storage overhead (~36 bytes vs 16 bytes) is acceptable for the scale.
- Generated via Python's `uuid.uuid4()`.

### 3.3 Why INTEGER for Booleans

SQLite has no native BOOLEAN type. Using INTEGER (0/1):
- Explicit and unambiguous.
- Indexable.
- SQLAlchemy `Boolean` type maps to this natively.

### 3.4 JSON Columns Strategy

JSON stored in TEXT columns for:
- **Flexible structured data** that doesn't need individual column queries (artifacts, breakdown, metadata).
- **Denormalized aggregates** that are read as a unit (category_breakdown, top_extensions).

JSON is NOT used for:
- Fields that need indexed queries.
- Fields that need individual column updates.
- Primary data that defines relationships.

### 3.5 Index Strategy

Indexes are designed to satisfy the <200ms NFR:
- **Covering indexes** for the most common dashboard queries (files by size, by category, by scan).
- **Partial indexes** (WHERE clause) to reduce index size for boolean flags.
- **Descending indexes** for "top N" queries.
- No index on `files.path` full content (too expensive for 1M rows). Use `directory` instead.

### 3.6 Cascade Delete Strategy

- `scans` → `files`: CASCADE (deleting a scan removes its files).
- `scans` → `folders`: CASCADE.
- `scans` → `duplicate_groups`: CASCADE.
- `duplicate_groups` → `duplicate_members`: CASCADE.
- `recommendations` → `cleanup_actions`: SET NULL (keep cleanup history even if recommendation is removed).
- `scans` → `recommendations`: SET NULL.

### 3.7 Data Volume Estimates

| Table | Rows (1M file scan) | Avg Row Size | Total Size |
|-------|---------------------|--------------|------------|
| files | 1,000,000 | ~400 bytes | ~400 MB |
| folders | ~100,000 | ~200 bytes | ~20 MB |
| duplicate_groups | ~5,000 | ~150 bytes | ~750 KB |
| duplicate_members | ~20,000 | ~150 bytes | ~3 MB |
| storage_snapshots | ~365/year | ~2 KB | ~730 KB |
| audit_logs | ~10,000/year | ~300 bytes | ~3 MB |
| **Total (estimated)** | | | **~425 MB** |

This is within acceptable limits for SQLite. WAL file adds ~1-2x during active writes.

---

## 4. PostgreSQL Migration Path

When the time comes to migrate to PostgreSQL:

1. **Schema changes needed:**
   - Replace TEXT timestamps with `TIMESTAMP WITH TIME ZONE`.
   - Replace TEXT UUIDs with `UUID` type.
   - Replace INTEGER booleans with `BOOLEAN`.
   - Replace TEXT JSON with `JSONB` (enables JSON path queries).
   - Replace partial index syntax.

2. **Migration approach:**
   - Create Alembic migration that generates PostgreSQL-compatible schema.
   - Data migration script: export SQLite → import PostgreSQL.
   - Change SQLAlchemy engine URL to `postgresql+asyncpg://...`.
   - Repository layer remains unchanged (SQLAlchemy abstracts dialect differences).

3. **What stays the same:**
   - All Pydantic schemas.
   - All service layer code.
   - All API routes.
   - All repository method signatures.

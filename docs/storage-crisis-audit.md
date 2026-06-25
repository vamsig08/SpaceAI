# Storage Crisis Audit

**Date:** 2026-06-24  
**Context:** SpaceAI is designed to help users who are running out of disk space. These users are the PRIMARY audience — yet the product currently fails when disk space is critically low.

---

## 1. Root Cause Analysis

### The Paradox

SpaceAI's target user is someone whose disk is nearly full. But the current architecture requires significant free space to operate:

| Operation | Disk Space Required | Why |
|-----------|-------------------|-----|
| SQLite DB for 1M files | ~1 GB | 400 bytes/row × 1M + indexes |
| WAL file during writes | ~200-500 MB | Grows unbounded during scan |
| WAL checkpoint | ~0 (in-place) | Reclaims WAL space |
| Duplicate hashing | 0 (streaming) | No disk writes |
| Post-scan pipeline | ~50 MB | Temporary query results |
| **Total for full scan** | **~1.5-2 GB** | Dominated by SQLite storage |

### What Actually Happened

1. User's disk had 131 MB free (of 228 GB total)
2. Scan started and processed 1,018,928 files successfully
3. SQLite DB grew to 1 GB, WAL grew to several hundred MB
4. Combined exceeded remaining space → `disk I/O error`
5. Scan marked as "failed" despite having valid data for 1M files
6. Subsequent API calls fail because DB is corrupted
7. **The product broke for EXACTLY the type of user it's meant to help**

---

## 2. Minimum Space Requirements

### Per Operation

| Operation | Minimum Free Space | Notes |
|-----------|-------------------|-------|
| Scan (10K files) | 50 MB | Small directories are fine |
| Scan (100K files) | 200 MB | Moderate home directory |
| Scan (500K files) | 700 MB | Large home directory |
| Scan (1M files) | 1.5 GB | Full developer workstation |
| Duplicate detection | ~0 extra | Writes to existing DB (minimal growth) |
| Stale analysis | ~0 extra | Updates in-place |
| Workspace detection | ~0 extra | In-memory analysis |
| Recommendations | ~0 extra | Small table |
| Cleanup (trash) | Same as files moved | Files relocated, not copied |

### The Critical Insight

**Scanning is the only operation that consumes significant disk space.** All other operations (duplicate detection, stale analysis, recommendations) operate on existing data with negligible additional storage.

---

## 3. Current Failure Points

### Where Low-Disk Corrupts State

| Failure Point | Consequence | Severity |
|---------------|-------------|----------|
| WAL grows beyond free space during scan | `disk I/O error`, DB corrupted | **Critical** |
| Checkpoint fails (no space for temp file) | WAL accumulates indefinitely | High |
| Scan finalization UPDATE fails | Data exists but status stays "running" forever | High |
| Frontend `.next` build cache | Build fails (unrelated to scan) | Low |
| Alembic migration on low space | Migration fails, DB in inconsistent state | High |

### Where Low-Disk Confuses Users

| Scenario | What User Sees | Reality |
|----------|----------------|---------|
| Scan "fails" after processing 1M files | "Scan failed" error | Data is actually in DB, just not marked complete |
| API returns 500 | "Something went wrong" | DB can't execute queries |
| Overview shows zeros | Appears broken | Snapshot wasn't generated due to disk full |
| Cleanup execution fails | "Cleanup failed" | Trash location can't be created (no space) |

### Where Low-Disk Breaks Cleanup Workflows

| Issue | Impact |
|-------|--------|
| `shutil.move()` to trash requires temp space on some filesystems | Cleanup fails for large files on cross-device moves |
| Audit log INSERT fails | Action executed but not recorded (safety gap) |
| Manifest JSON write fails | Rollback becomes impossible |

---

## 4. Preflight Protections (Current vs Required)

### Current Implementation

```python
# In crawler.py — basic warning only
disk_usage = os.statvfs(root_path)
free_bytes = disk_usage.f_bavail * disk_usage.f_frsize
min_required = 500 * 1024 * 1024  # 500 MB
if free_bytes < min_required:
    logger.warning("scan_low_disk_space", ...)
```

**Problems:**
1. Warning is logged but not surfaced to user
2. Scan proceeds anyway (no blocking)
3. 500 MB threshold is too low for large scans
4. No dynamic estimation based on scan size

### Required Protections

| Check | When | Action |
|-------|------|--------|
| Free space < 100 MB | Before scan starts | **BLOCK** — return 422 with clear message |
| Free space < estimated DB size | Before scan starts | **WARN** — proceed with smaller batch size |
| Free space < 50 MB during scan | Every checkpoint | **PAUSE** — flush, checkpoint WAL, warn user |
| Free space < 20 MB during scan | Every batch | **STOP GRACEFULLY** — mark as `completed_partial` |
| Free space < 10 MB on cleanup execute | Before moving files | **BLOCK** — trash on same filesystem only |

---

## 5. Recovery Mode Design

### Concept

When disk space is critically low (< 500 MB), SpaceAI should automatically enter "Recovery Mode" — a lightweight operating mode optimized for helping users free space without requiring significant disk overhead.

### Recovery Mode Behavior

| Feature | Normal Mode | Recovery Mode |
|---------|------------|---------------|
| Scan strategy | Full BFS, write all metadata | **Lightweight scan**: count + size only, no full metadata |
| DB storage | Full SQLite with WAL | **In-memory summary** with optional disk persist |
| Scan scope | Entire directory | **Top-N largest dirs only** (quick wins) |
| Analysis | Full duplicate hash | **Size-only duplicate candidates** (no hashing) |
| Output | Full dashboard | **Recovery dashboard**: largest files, quick-cleanup suggestions |
| Cleanup | Trash-first (needs space) | **Direct delete with confirmation** (no trash) |

### Recovery Mode Triggers

```
Free space < 500 MB → Enter Recovery Mode
Free space > 2 GB   → Exit Recovery Mode (offer full scan)
```

### Recovery Mode UI

Replace the normal dashboard with a focused "Low Space Recovery" view:
```
⚠️ Your disk has only 131 MB free.

SpaceAI is in Recovery Mode — limited analysis, focused on quick wins.

Quick Actions:
  📁 Largest files:       model.pt (5.2 GB) — [Delete]
  📁 Trash:              1.8 GB in Trash — [Empty Trash]
  📁 node_modules:       3.2 GB across 8 projects — [Clean All]
  📁 Docker:             4.1 GB unused images — [Remove]
  📁 Downloads:          12.3 GB, 89 files >100MB — [Review]

Estimated recovery: 26.6 GB available
```

---

## 6. Is SQLite Correct for This Use Case?

### Assessment

| Factor | SQLite Advantage | SQLite Risk |
|--------|-----------------|-------------|
| Zero infrastructure | ✓ No server needed | |
| Single file | ✓ Easy backup | ✗ File grows to 1GB for 1M files |
| WAL mode | ✓ Concurrent read/write | ✗ WAL can grow unbounded |
| Query performance | ✓ Fast with indexes | |
| Low-disk operation | | ✗ **Cannot write when disk is full** |

### Verdict

SQLite is correct for normal operation but needs safeguards for low-disk scenarios:

1. **WAL checkpointing** (already implemented) prevents unbounded growth
2. **Streaming results** (already done) keeps memory low
3. **Graceful degradation** (needs implementation) handles disk-full mid-scan
4. **Recovery mode** (not implemented) provides value without heavy DB writes

### Alternative Considered: Store DB on /tmp or RAM

Not viable:
- /tmp may be on the same filesystem
- RAM disk requires same memory budget we're trying to stay under
- Moving DB to another volume introduces complexity

---

## 7. Beta-Blocking Issues

| # | Issue | Severity | Fix Effort |
|---|-------|----------|-----------|
| 1 | Scan crashes and corrupts DB when disk full | **CRITICAL** | 1 hour |
| 2 | No user-visible warning before scan starts | **HIGH** | 30 min |
| 3 | Corrupted DB requires manual file deletion to recover | **HIGH** | 30 min |
| 4 | Cleanup trash operation fails silently on low space | **MEDIUM** | 20 min |
| 5 | No recovery mode for critically-low-space users | **LOW for beta** | 4+ hours |

---

## 8. Recommended Safeguards (Prioritized)

### Must Have for Beta

1. **Hard block at 100 MB free** — Return HTTP 422 with message: "Insufficient disk space to scan. Free at least 500 MB before scanning, or scan a smaller directory."

2. **Graceful stop on disk full during scan** — Instead of crashing, detect the `OperationalError` in the batch writer, flush what's possible, mark scan as `completed_partial`, and surface a message: "Scan stopped early due to low disk space. X files were analyzed."

3. **Auto-recovery of corrupted DB** — On startup, if DB fails to open, rename the corrupt file and create a fresh one. Log the event. User loses scan history but can immediately re-scan.

4. **Estimate scan size in preflight** — Quick `du -s` style check (count files in first 1000 dirs) to estimate whether the scan will exceed available space. Warn if estimated DB size > 50% of free space.

### Should Have for Beta

5. **WAL size monitoring** — During scan, check WAL file size. If it exceeds 500 MB, force a checkpoint.

6. **Cleanup on same filesystem** — When trashing files, verify trash location is on the same filesystem as source. Cross-device moves require temp space.

### Post-Beta (Recovery Mode)

7. **Lightweight scan** — For disks with < 500 MB free, skip full metadata collection. Just find the top 100 largest files and common cleanup targets.

8. **Direct delete option** — For critically-low users, offer "delete permanently" (skip trash) with extra confirmation.

---

## 9. GO / NO-GO Assessment for Low-Storage Users

### Current State: NO-GO for users with < 200 MB free

The product will crash, corrupt its database, and leave the user worse off (DB file consumes additional space without providing value).

### After Safeguards #1-3: CONDITIONAL GO

Users with < 200 MB will be blocked from full scans (correct behavior) but should be directed to manual cleanup. Users with 500 MB - 2 GB free can scan smaller directories safely.

### After Recovery Mode: FULL GO

All users can benefit regardless of available space.

---

## 10. Implementation Priority

| Priority | Fix | User Impact |
|----------|-----|-------------|
| P0 (before beta) | Hard block at 100 MB + graceful disk-full handling | Prevents corruption |
| P0 (before beta) | API returns clear error message (not 500) | Users understand what's wrong |
| P1 (first patch) | Estimate scan size + warn | Users make informed decisions |
| P1 (first patch) | Auto-recovery of corrupt DB on startup | Self-healing |
| P2 (post-beta) | Recovery mode for critically-low users | Full audience coverage |

---

## Executive Summary

SpaceAI's target audience is people running out of disk space. The product currently **fails for this audience** because it requires 1-2 GB of free space to scan a typical home directory. This is a fundamental product-market fit issue that must be addressed.

For beta, the minimum viable fix is:
1. Block scans when space is too low (with clear messaging)
2. Handle disk-full gracefully during scan (don't corrupt)
3. Recover automatically from corruption

These three fixes (2 hours total) make the product safe for users with > 500 MB free while clearly communicating limitations to users with less. Recovery Mode (post-beta) extends coverage to all users.

**Beta verdict: GO with safeguards #1-3 implemented.** The product provides genuine value to users with 500 MB+ free space, which covers the majority of "running low" scenarios (most users notice at 5-10 GB free, not 100 MB).

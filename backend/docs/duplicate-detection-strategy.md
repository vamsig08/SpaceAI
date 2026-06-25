# Duplicate Detection Strategy — Phase 3

**Date:** 2026-06-23  
**Status:** Accepted

---

## 1. Strategy Comparison

### 1.1 Metadata-Only Filtering (Stage 1 — Size Grouping)

| Aspect | Assessment |
|--------|-----------|
| **Approach** | Group files by `size_bytes`. Only files sharing the exact same size can be duplicates. |
| **Effectiveness** | Eliminates ~95% of files from further processing. Unique-sized files cannot be duplicates. |
| **Cost** | Near-zero: single indexed SQL query on `(scan_id, size_bytes)`. |
| **False positives** | High (same-size ≠ same content). Must be refined by hashing. |
| **Memory** | Negligible — query returns only candidate group metadata. |

**Verdict**: Mandatory first stage. Cost is trivially low and eliminates the vast majority of files.

### 1.2 Partial Hashing (Stage 2 — First 4KB + Last 4KB)

| Aspect | Assessment |
|--------|-----------|
| **Approach** | Read first 4KB and last 4KB of each candidate file, hash the combined 8KB. |
| **Effectiveness** | Catches ~80% of non-duplicates that survived size-grouping (different headers, footers, or metadata). |
| **Cost** | 2 seeks + 8KB read per file. For 50K candidates: 50K × 8KB = 400MB I/O (fast, sequential-friendly). |
| **False positives** | Low but non-zero (files identical in first/last 4KB but different in middle — rare in practice). |
| **Memory** | 8KB per file in-flight. Negligible. |

**Verdict**: Highly recommended. Eliminates most false candidates at <1% of the I/O cost of full hashing. Critical for large media files where full SHA256 would take minutes per file.

### 1.3 Full-File SHA256 Hashing (Stage 3 — Definitive)

| Aspect | Assessment |
|--------|-----------|
| **Approach** | Stream entire file through SHA256 in 64KB chunks. |
| **Effectiveness** | Definitive — cryptographic collision probability is negligible (~2^-128). |
| **Cost** | Full disk read. For 10K confirmed candidates averaging 5MB: 50GB I/O. |
| **False positives** | Zero for practical purposes. |
| **Memory** | 64KB buffer per concurrent hash operation. At 4 threads: 256KB. |

**Verdict**: Required as final confirmation stage. Only applied to files that passed Stages 1 and 2.

### 1.4 Multi-Stage Pipeline (Selected Strategy)

```
Stage 1: Size Grouping (SQL)
  │ 1M files → ~50K candidates (5% survival rate)
  ▼
Stage 2: Partial Hash (First 4KB + Last 4KB)
  │ 50K candidates → ~15K confirmed candidates
  ▼
Stage 3: Full SHA256 (streaming, 64KB chunks)
  │ 15K candidates → 8K true duplicates in ~3K groups
  ▼
Stage 4: Group Formation + Wasted Space Calculation
  │ Write duplicate_groups + duplicate_members
  ▼
Done
```

---

## 2. Architecture Decisions

### ADR-011: Multi-Stage Duplicate Detection Pipeline

**Decision**: Implement a 3-stage detection pipeline (size → partial hash → full hash) rather than jumping directly to full SHA256.

**Reasoning**:
- Full SHA256 of 1M files would require reading ~500GB+ of data. At 200MB/s sequential read, that's 40+ minutes of pure I/O.
- Size filtering (Stage 1) eliminates 95% of files with a single SQL query (~100ms).
- Partial hashing (Stage 2) eliminates 70% of remaining candidates with 8KB reads per file.
- Only ~1.5% of original files reach the expensive full-hash stage.

**Alternatives rejected**:
- **Full hash everything**: 40+ min I/O for 1M files. Violates user experience expectations.
- **Size-only then full hash**: Skips partial hashing. Works but hashes 3x more files than needed.
- **Content-defined chunking (like rsync)**: Overkill — we need binary identity, not similarity.

**Tradeoffs**:
- Three stages add implementation complexity vs. single-pass full hash.
- Partial hash introduces a tiny false-positive window (mitigated by Stage 3 confirmation).
- Accepted because: 10-20x reduction in total I/O justifies the additional code path.

### ADR-012: Streaming Hasher with Configurable Buffer

**Decision**: Hash files using a streaming approach with 64KB read buffers. Never load an entire file into memory.

**Reasoning**:
- Must support files >10GB without memory impact.
- 64KB aligns with OS page size and SQLite page size for cache efficiency.
- At 4 concurrent threads × 64KB = 256KB total memory for hashing — negligible.
- Python's `hashlib.sha256()` accepts incremental `update()` calls natively.

### ADR-013: Checkpoint Recovery for Hash Pass

**Decision**: Hash progress is checkpointed by tracking which size-groups have been fully processed. On interruption, resume from the last incomplete group rather than re-hashing completed files.

**Reasoning**:
- The `files.sha256_hash` column persists hashes. Files already hashed don't need re-processing.
- Recovery is simple: query for candidates where `sha256_hash IS NULL AND size_bytes IN (candidate sizes)`.
- No separate checkpoint table needed — the hash column itself is the checkpoint.

### ADR-014: Incremental Duplicate Detection

**Decision**: On incremental rescans, only process newly discovered files (those without a hash) and compare against the existing hash set.

**Reasoning**:
- Files already hashed retain their hash across scans (if unchanged).
- New files are checked against existing `sha256_hash` values in the DB.
- Modified files (detected by `modified_at` change) have their hash cleared and re-computed.
- This means duplicate detection on incremental scans processes only the delta.

---

## 3. Detailed Design

### 3.1 Memory Budget at 1M Files

| Component | Peak Memory | Notes |
|-----------|-------------|-------|
| Size-group query result | ~2 MB | Returns (size, count) pairs, not file records |
| Candidate file IDs | ~5 MB | 50K UUIDs × 36 bytes × 3 (id, path, size) |
| Partial hash buffers | 32 KB | 4 threads × 8KB |
| Full hash buffers | 256 KB | 4 threads × 64KB |
| Duplicate group objects | ~1 MB | ~3K groups × 300 bytes |
| **Total** | **~9 MB** | Well within 500MB budget |

### 3.2 I/O Budget at 1M Files

| Stage | Files Processed | I/O per File | Total I/O |
|-------|-----------------|--------------|-----------|
| Size grouping | 0 (SQL only) | 0 | 0 |
| Partial hash | ~50K | 8 KB | 400 MB |
| Full hash | ~15K | avg 5 MB | ~75 GB |
| **Total** | | | **~75 GB** |

At 200 MB/s sequential SSD read: ~6 minutes for full hash pass.
NFR target (30 min for 1M files) is for scan, not hash. Hash pass is a separate user-triggered operation.

### 3.3 Database Integration

Reuses existing tables from migration 002:
- `duplicate_groups`: Groups sharing the same SHA256 hash
- `duplicate_members`: Individual files within each group
- `files.sha256_hash`: Persists computed hashes
- `files.is_duplicate`: Flag for quick filtering

### 3.4 Task Integration

- Hash pass runs as `TaskType.HASH` in the existing TaskManager.
- Progress reported via existing `ProgressReporter` (SSE).
- Cancellation via cooperative `cancel_event` checking between file batches.
- Concurrency: max 1 hash task at a time (semaphore already configured).

### 3.5 API Design

```
POST   /api/v1/duplicates/detect          → Start detection (returns task_id)
GET    /api/v1/duplicates                  → List duplicate groups (paginated)
GET    /api/v1/duplicates/summary          → Overview stats
GET    /api/v1/duplicates/{group_id}       → Group details with members
POST   /api/v1/duplicates/{group_id}/resolve → Mark keeper, propose cleanup
```

---

## 4. Performance Estimates

| Dataset | Stage 1 (SQL) | Stage 2 (Partial) | Stage 3 (Full) | Total |
|---------|---------------|--------------------|--------------------|-------|
| 10K files | <100ms | ~2s | ~30s | ~32s |
| 100K files | ~200ms | ~15s | ~3min | ~3.5min |
| 500K files | ~500ms | ~60s | ~12min | ~13.5min |
| 1M files | ~1s | ~2min | ~25min | ~28min |

These are estimates assuming SSD storage. HDD would be 3-5x slower on Stage 3.

---

## 5. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Files deleted/moved between scan and hash pass | Low | Graceful OSError handling; skip and log |
| Hash collisions (SHA256) | Negligible | 2^-128 probability; not a practical concern |
| Very large files (>10GB) blocking thread pool | Medium | Per-file timeout; report as "skipped" after 5 min |
| Symlink duplicates (same inode) | Low | Compare (device, inode) to detect hardlinks before hashing |
| Permission denied on hash read | Low | Skip and record in error count |

---

## 6. Summary

**Selected strategy**: 3-stage pipeline (size → partial hash → full SHA256)  
**Expected I/O reduction**: ~99.5% vs naive full-hash approach  
**Memory footprint**: <10 MB for duplicate detection logic  
**Integration**: Reuses TaskManager, ProgressReporter, existing DB tables  
**Recovery**: Hash column IS the checkpoint — resume is automatic

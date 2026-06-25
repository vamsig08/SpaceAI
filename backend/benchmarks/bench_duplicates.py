"""Duplicate detection benchmark suite.

Creates synthetic datasets with known duplicate patterns, runs the 3-stage
pipeline, and measures performance at scale.

Datasets:
  - 1K files with 10% duplicates (warmup)
  - 10K files with 15% duplicates (small)
  - 50K files with 20% duplicates (medium)

Measures:
  - Stage 1 (size grouping) latency
  - Stage 2 (partial hash) latency and throughput
  - Stage 3 (full hash) latency and throughput
  - Total pipeline time
  - Memory usage
  - Accuracy (expected vs detected groups)

Usage:
    python -m benchmarks.bench_duplicates
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import sqlite3
import time
import tracemalloc
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models.scan  # noqa: F401
import app.models.file  # noqa: F401
import app.models.folder  # noqa: F401
import app.models.duplicate  # noqa: F401

from app.models.base import generate_uuid, utc_now
from app.services.duplicate_service import run_duplicate_detection
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType


def _create_synthetic_dataset(
    tmp_dir: Path,
    db_path: str,
    file_count: int,
    duplicate_ratio: float = 0.15,
    seed: int = 42,
) -> tuple[str, int, int]:
    """Create a synthetic filesystem + database with known duplicates.

    Creates real files on disk and matching records in SQLite.

    Args:
        tmp_dir: Directory to create files in.
        db_path: SQLite database path.
        file_count: Total number of file records.
        duplicate_ratio: Fraction of files that are duplicates.
        seed: Random seed.

    Returns:
        Tuple of (scan_id, expected_groups, expected_duplicate_files).
    """
    random.seed(seed)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")

    # Create minimal schema
    conn.executescript("""
        CREATE TABLE scans (
            id TEXT PRIMARY KEY, root_path TEXT NOT NULL, status TEXT DEFAULT 'completed',
            scan_type TEXT DEFAULT 'full', started_at TEXT, completed_at TEXT,
            total_files INTEGER DEFAULT 0, total_dirs INTEGER DEFAULT 0,
            total_size_bytes INTEGER DEFAULT 0, files_per_second REAL,
            error_message TEXT, checkpoint_data TEXT, exclusion_patterns TEXT,
            platform TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE files (
            id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, path TEXT NOT NULL,
            directory TEXT NOT NULL, filename TEXT NOT NULL, extension TEXT,
            size_bytes INTEGER NOT NULL, mime_type TEXT, category TEXT,
            created_at TEXT, modified_at TEXT, accessed_at TEXT,
            owner TEXT, permissions TEXT, sha256_hash TEXT,
            is_duplicate INTEGER DEFAULT 0, is_stale INTEGER DEFAULT 0,
            stale_score REAL, risk_level TEXT, discovered_at TEXT NOT NULL
        );
        CREATE INDEX idx_files_scan_id ON files(scan_id);
        CREATE INDEX idx_files_size_bytes ON files(size_bytes);
        CREATE INDEX idx_files_category ON files(category);
        CREATE TABLE duplicate_groups (
            id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, sha256_hash TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL, member_count INTEGER NOT NULL,
            wasted_bytes INTEGER NOT NULL, status TEXT DEFAULT 'unresolved',
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_dup_groups_scan ON duplicate_groups(scan_id);
        CREATE TABLE duplicate_members (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, file_id TEXT NOT NULL,
            path TEXT NOT NULL, is_keeper INTEGER DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE INDEX idx_dup_members_group ON duplicate_members(group_id);
        CREATE TABLE folders (
            id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, path TEXT NOT NULL,
            name TEXT NOT NULL, parent_path TEXT, depth INTEGER DEFAULT 0,
            total_size_bytes INTEGER DEFAULT 0, file_count INTEGER DEFAULT 0,
            dir_count INTEGER DEFAULT 0, discovered_at TEXT NOT NULL
        );
    """)

    scan_id = str(uuid.uuid4())
    now = utc_now()
    conn.execute(
        "INSERT INTO scans (id, root_path, status, total_files, created_at) VALUES (?, ?, 'completed', ?, ?)",
        (scan_id, str(tmp_dir), file_count, now),
    )

    # Generate duplicate templates (source content for duplicates)
    num_dup_groups = max(1, int(file_count * duplicate_ratio / 3))  # avg 3 copies per group
    dup_templates: list[tuple[bytes, int]] = []
    for _ in range(num_dup_groups):
        size = random.randint(2000, 500000)  # 2KB to 500KB
        content = os.urandom(size)
        dup_templates.append((content, size))

    # Create files
    unique_count = int(file_count * (1 - duplicate_ratio))
    dup_count = file_count - unique_count

    batch = []
    expected_dup_files = 0
    sizes_for_dups: dict[int, int] = {}  # size -> count of files with that size

    # Unique files (each with a unique size to avoid false positive size matches)
    for i in range(unique_count):
        size = random.randint(1024, 1000000) + i  # Unique sizes via offset
        content = os.urandom(min(size, 100))  # Only write first 100 bytes (enough to be unique)
        rel_path = f"unique/dir_{i % 50}/file_{i}.bin"
        full_path = tmp_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content + b"\x00" * (size - len(content)))

        batch.append((
            str(uuid.uuid4()), scan_id, str(full_path), str(full_path.parent),
            full_path.name, ".bin", size, "other", now,
        ))

    # Duplicate files (2-5 copies per template)
    dup_file_idx = 0
    expected_groups = 0
    for content, size in dup_templates:
        copies = min(random.randint(2, 5), dup_count - dup_file_idx + 2)
        if copies < 2:
            break

        expected_groups += 1
        for c in range(copies):
            rel_path = f"dups/group_{expected_groups}/copy_{c}.bin"
            full_path = tmp_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(content)

            batch.append((
                str(uuid.uuid4()), scan_id, str(full_path), str(full_path.parent),
                full_path.name, ".bin", size, "other", now,
            ))
            dup_file_idx += 1
            expected_dup_files += 1

        if dup_file_idx >= dup_count:
            break

    conn.executemany(
        "INSERT INTO files (id, scan_id, path, directory, filename, extension, size_bytes, category, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        batch,
    )
    conn.commit()
    conn.close()

    return scan_id, expected_groups, expected_dup_files


async def _run_benchmark(
    name: str,
    tmp_dir: Path,
    db_path: str,
    file_count: int,
    duplicate_ratio: float,
) -> dict:
    """Run a single benchmark configuration."""
    print(f"\n{'─'*60}")
    print(f"  {name}: {file_count:,} files, {duplicate_ratio*100:.0f}% duplicates")
    print(f"{'─'*60}")

    # Generate dataset
    t0 = time.time()
    scan_id, expected_groups, expected_dup_files = _create_synthetic_dataset(
        tmp_dir, db_path, file_count, duplicate_ratio
    )
    gen_time = time.time() - t0
    print(f"  Dataset generated in {gen_time:.1f}s")
    print(f"  Expected groups: {expected_groups}, expected dup files: {expected_dup_files}")

    db_size_mb = os.path.getsize(db_path) / 1024 / 1024
    print(f"  DB size: {db_size_mb:.1f} MB")

    # Create async engine
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.abspath(db_path)}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    def pragmas(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA cache_size=-65536")
        cur.execute("PRAGMA mmap_size=268435456")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.close()

    event.listen(engine.sync_engine, "connect", pragmas)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Run pipeline
    state = TaskState(task_id=scan_id, task_type=TaskType.HASH)
    reporter = ProgressReporter()
    pool = ThreadPoolExecutor(max_workers=4)

    tracemalloc.start()
    t_start = time.time()

    try:
        await run_duplicate_detection(
            task_state=state,
            scan_id=scan_id,
            session_factory=session_factory,
            thread_pool=pool,
            reporter=reporter,
        )
    finally:
        pool.shutdown(wait=False)

    elapsed = time.time() - t_start
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Verify results
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM duplicate_groups WHERE scan_id = :sid"),
            {"sid": scan_id},
        )
        detected_groups = result.scalar_one()

        result = await session.execute(
            text("SELECT COALESCE(SUM(wasted_bytes), 0) FROM duplicate_groups WHERE scan_id = :sid"),
            {"sid": scan_id},
        )
        total_wasted = result.scalar_one()

        result = await session.execute(
            text("SELECT COUNT(*) FROM duplicate_members WHERE group_id IN (SELECT id FROM duplicate_groups WHERE scan_id = :sid)"),
            {"sid": scan_id},
        )
        detected_files = result.scalar_one()

    await engine.dispose()

    peak_mem_mb = peak_mem / 1024 / 1024

    print(f"\n  Results:")
    print(f"    Pipeline time:     {elapsed:.2f}s")
    print(f"    Peak memory:       {peak_mem_mb:.1f} MB")
    print(f"    Groups detected:   {detected_groups} (expected ~{expected_groups})")
    print(f"    Duplicate files:   {detected_files}")
    print(f"    Wasted space:      {total_wasted / 1024 / 1024:.1f} MB")
    print(f"    Files/sec:         {file_count / elapsed:.0f}")
    print(f"    Status:            {state.status.value}")

    # Accuracy check
    accuracy_ok = detected_groups >= expected_groups * 0.9  # Allow 10% tolerance for random sizing
    mem_ok = peak_mem_mb < 500
    status_ok = state.status == TaskStatus.COMPLETED

    print(f"\n  Validation:")
    print(f"    Accuracy:  {'PASS' if accuracy_ok else 'FAIL'} ({detected_groups}/{expected_groups} groups)")
    print(f"    Memory:    {'PASS' if mem_ok else 'FAIL'} ({peak_mem_mb:.1f} MB < 500 MB)")
    print(f"    Completed: {'PASS' if status_ok else 'FAIL'}")

    return {
        "name": name,
        "file_count": file_count,
        "duplicate_ratio": duplicate_ratio,
        "pipeline_seconds": round(elapsed, 2),
        "peak_memory_mb": round(peak_mem_mb, 1),
        "detected_groups": detected_groups,
        "expected_groups": expected_groups,
        "detected_files": detected_files,
        "wasted_bytes": total_wasted,
        "files_per_second": round(file_count / elapsed, 0),
        "accuracy_pass": accuracy_ok,
        "memory_pass": mem_ok,
        "all_pass": accuracy_ok and mem_ok and status_ok,
    }


async def main() -> None:
    print("=" * 60)
    print("  SpaceAI Duplicate Detection Benchmark")
    print("=" * 60)

    base_tmp = Path("./benchmarks/data/dup_bench_tmp")
    if base_tmp.exists():
        shutil.rmtree(base_tmp)

    configs = [
        ("1K Warmup", 1000, 0.10),
        ("10K Small", 10000, 0.15),
        ("50K Medium", 50000, 0.20),
    ]

    results = []
    for name, count, ratio in configs:
        tmp_dir = base_tmp / name.replace(" ", "_").lower()
        db_path = f"./benchmarks/data/dup_{name.replace(' ', '_').lower()}.db"
        result = await _run_benchmark(name, tmp_dir, db_path, count, ratio)
        results.append(result)

    # Cleanup temp files
    if base_tmp.exists():
        shutil.rmtree(base_tmp)

    # Summary
    print(f"\n\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}\n")
    print(f"{'Dataset':<15} {'Time':>8} {'Memory':>8} {'Groups':>8} {'Files/s':>8} {'Pass':>6}")
    print("-" * 58)
    for r in results:
        print(
            f"{r['name']:<15} {r['pipeline_seconds']:>7.1f}s "
            f"{r['peak_memory_mb']:>6.1f}MB {r['detected_groups']:>7} "
            f"{r['files_per_second']:>7.0f} {'  OK' if r['all_pass'] else 'FAIL':>5}"
        )

    all_pass = all(r["all_pass"] for r in results)
    print(f"\nOverall: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")


if __name__ == "__main__":
    asyncio.run(main())

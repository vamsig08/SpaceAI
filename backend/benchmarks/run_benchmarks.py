"""Performance benchmark suite for SpaceAI analytics queries.

Measures query latency, memory usage, and throughput for the repository
and service layers against datasets of varying scale.

Usage:
    python -m benchmarks.run_benchmarks

Generates datasets at 10K, 100K, and 500K scale, then benchmarks all
analytics queries against each.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import resource
import sys
import time
import tracemalloc
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.generate_dataset import generate_dataset

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.snapshot_repository import SnapshotRepository
from app.services.analytics_service import AnalyticsService


# ─── Benchmark Configuration ──────────────────────────────────────────────────

DATASETS = [
    {"name": "10K", "count": 10_000},
    {"name": "100K", "count": 100_000},
    {"name": "500K", "count": 500_000},
]

BENCHMARK_ITERATIONS = 5  # Run each query N times, report median


# ─── Benchmark Harness ────────────────────────────────────────────────────────

class BenchmarkResult:
    def __init__(self, name: str, dataset: str) -> None:
        self.name = name
        self.dataset = dataset
        self.latencies_ms: list[float] = []
        self.memory_peak_kb: float = 0
        self.row_count: int = 0

    @property
    def median_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        s = sorted(self.latencies_ms)
        return s[len(s) // 2]

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        s = sorted(self.latencies_ms)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]

    @property
    def min_ms(self) -> float:
        return min(self.latencies_ms) if self.latencies_ms else 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dataset": self.dataset,
            "median_ms": round(self.median_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "min_ms": round(self.min_ms, 2),
            "memory_peak_kb": round(self.memory_peak_kb, 1),
            "iterations": len(self.latencies_ms),
            "passes_200ms": self.median_ms < 200,
        }


async def run_single_benchmark(
    name: str,
    dataset_name: str,
    session_factory: async_sessionmaker[AsyncSession],
    scan_id: str,
    query_fn,
    iterations: int = BENCHMARK_ITERATIONS,
) -> BenchmarkResult:
    """Run a single benchmark query multiple times and collect results."""
    result = BenchmarkResult(name, dataset_name)

    for i in range(iterations):
        tracemalloc.start()
        start = time.perf_counter()

        async with session_factory() as session:
            await query_fn(session, scan_id)

        elapsed_ms = (time.perf_counter() - start) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        result.latencies_ms.append(elapsed_ms)
        result.memory_peak_kb = max(result.memory_peak_kb, peak / 1024)

    return result


# ─── Query Functions ──────────────────────────────────────────────────────────

async def query_largest_files(session: AsyncSession, scan_id: str) -> None:
    repo = FileRepository(session)
    await repo.find_largest(scan_id, limit=100)


async def query_largest_files_top10(session: AsyncSession, scan_id: str) -> None:
    repo = FileRepository(session)
    await repo.find_largest(scan_id, limit=10)


async def query_category_breakdown(session: AsyncSession, scan_id: str) -> None:
    repo = FileRepository(session)
    await repo.get_category_breakdown(scan_id)


async def query_extension_breakdown(session: AsyncSession, scan_id: str) -> None:
    repo = FileRepository(session)
    await repo.get_extension_breakdown(scan_id, limit=20)


async def query_total_stats(session: AsyncSession, scan_id: str) -> None:
    repo = FileRepository(session)
    await repo.get_total_stats(scan_id)


async def query_largest_folders(session: AsyncSession, scan_id: str) -> None:
    repo = FolderRepository(session)
    await repo.find_largest(scan_id, limit=50)


async def query_analytics_overview(session: AsyncSession, scan_id: str) -> None:
    """Simulates the full overview computation (without disk_usage)."""
    file_repo = FileRepository(session)
    folder_repo = FolderRepository(session)
    await file_repo.get_total_stats(scan_id)
    await file_repo.get_category_breakdown(scan_id)
    await folder_repo.get_total_count(scan_id)


# ─── Main Runner ──────────────────────────────────────────────────────────────

async def run_dataset_benchmarks(dataset_config: dict) -> list[BenchmarkResult]:
    """Generate dataset and run all benchmarks against it."""
    name = dataset_config["name"]
    count = dataset_config["count"]
    db_path = f"./benchmarks/data/bench_{name.lower()}.db"

    print(f"\n{'='*60}")
    print(f"  DATASET: {name} ({count:,} files)")
    print(f"{'='*60}")

    # Generate dataset
    random.seed(42)
    stats = generate_dataset(count, db_path)
    scan_id = stats["scan_id"]

    # Create async engine for benchmarks
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.abspath(db_path)}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Apply PRAGMAs
    from sqlalchemy import event
    def set_pragmas(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA cache_size=-65536")
        c.execute("PRAGMA mmap_size=268435456")
        c.execute("PRAGMA temp_store=MEMORY")
        c.close()

    event.listen(engine.sync_engine, "connect", set_pragmas)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Warm up (first query loads pages into cache)
    async with session_factory() as sess:
        await sess.execute(text("SELECT COUNT(*) FROM files"))

    # Run benchmarks
    queries = [
        ("Largest Files (top 100)", query_largest_files),
        ("Largest Files (top 10)", query_largest_files_top10),
        ("Category Breakdown", query_category_breakdown),
        ("Extension Breakdown (top 20)", query_extension_breakdown),
        ("Total Stats (COUNT + SUM)", query_total_stats),
        ("Largest Folders (top 50)", query_largest_folders),
        ("Full Overview (combined)", query_analytics_overview),
    ]

    results: list[BenchmarkResult] = []
    print(f"\nRunning {len(queries)} queries × {BENCHMARK_ITERATIONS} iterations...\n")
    print(f"{'Query':<35} {'Median':>8} {'P95':>8} {'Min':>8} {'Mem KB':>8} {'<200ms':>7}")
    print("-" * 78)

    for query_name, query_fn in queries:
        result = await run_single_benchmark(
            query_name, name, session_factory, scan_id, query_fn
        )
        results.append(result)

        status = "  OK" if result.median_ms < 200 else "SLOW"
        print(
            f"{query_name:<35} {result.median_ms:>7.1f}ms {result.p95_ms:>7.1f}ms "
            f"{result.min_ms:>7.1f}ms {result.memory_peak_kb:>7.0f} {status:>6}"
        )

    await engine.dispose()

    # Report dataset stats
    print(f"\nDataset stats:")
    print(f"  DB file size: {stats['db_size_mb']:.1f} MB")
    print(f"  Generation speed: {stats['records_per_second']:.0f} records/sec")

    return results


async def main() -> None:
    """Run full benchmark suite across all dataset sizes."""
    print("=" * 60)
    print("  SpaceAI Performance Benchmark Suite")
    print("=" * 60)

    all_results: list[BenchmarkResult] = []

    for dataset in DATASETS:
        results = await run_dataset_benchmarks(dataset)
        all_results.extend(results)

    # Summary report
    print(f"\n\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}\n")

    # Check NFR: all queries < 200ms
    failures = [r for r in all_results if r.median_ms >= 200]
    passes = [r for r in all_results if r.median_ms < 200]

    print(f"Total benchmarks: {len(all_results)}")
    print(f"  Passing (<200ms): {len(passes)}")
    print(f"  Failing (≥200ms): {len(failures)}")

    if failures:
        print(f"\n  FAILURES (NFR violation: API response >200ms):")
        for f in failures:
            print(f"    [{f.dataset}] {f.name}: {f.median_ms:.1f}ms (median)")

    # Save results to JSON
    os.makedirs("./benchmarks/results", exist_ok=True)
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "datasets": [d["name"] for d in DATASETS],
        "iterations": BENCHMARK_ITERATIONS,
        "results": [r.to_dict() for r in all_results],
        "nfr_pass": len(failures) == 0,
    }
    with open("./benchmarks/results/latest.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to benchmarks/results/latest.json")
    print(f"\nNFR Assessment: {'PASS' if not failures else 'NEEDS OPTIMIZATION'}")


if __name__ == "__main__":
    asyncio.run(main())

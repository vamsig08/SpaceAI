"""Synthetic dataset generator for SpaceAI performance benchmarks.

Generates realistic file metadata records directly into an SQLite database,
mimicking a developer workstation's file distribution:

- Documents, images, videos, archives, source code
- Git repositories, node_modules, Python venvs
- Docker artifacts, ML model files
- Realistic size distributions per category

Usage:
    python -m benchmarks.generate_dataset --count 10000 --output ./benchmarks/data/bench_10k.db
    python -m benchmarks.generate_dataset --count 100000 --output ./benchmarks/data/bench_100k.db
    python -m benchmarks.generate_dataset --count 500000 --output ./benchmarks/data/bench_500k.db
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
import uuid
from pathlib import Path

# ─── Distribution Configuration ───────────────────────────────────────────────

# Category distribution (must sum to 1.0)
CATEGORY_DISTRIBUTION: dict[str, float] = {
    "code": 0.35,       # Source code files
    "document": 0.10,   # PDFs, docs, text
    "image": 0.12,      # Photos, screenshots
    "video": 0.03,      # Large video files
    "audio": 0.03,      # Music/podcasts
    "archive": 0.05,    # Zip, tar, etc.
    "data": 0.07,       # DB, ML models, datasets
    "other": 0.25,      # Misc: configs, lock files, caches
}

# Size distributions (min, max) in bytes per category
SIZE_RANGES: dict[str, tuple[int, int]] = {
    "code": (100, 100_000),              # 100B to 100KB
    "document": (10_000, 50_000_000),    # 10KB to 50MB
    "image": (50_000, 20_000_000),       # 50KB to 20MB
    "video": (10_000_000, 2_000_000_000),  # 10MB to 2GB
    "audio": (1_000_000, 50_000_000),    # 1MB to 50MB
    "archive": (100_000, 500_000_000),   # 100KB to 500MB
    "data": (1_000, 5_000_000_000),      # 1KB to 5GB (ML models)
    "other": (100, 1_000_000),           # 100B to 1MB
}

# Extensions per category (weighted by likelihood)
EXTENSIONS: dict[str, list[str]] = {
    "code": [".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
             ".c", ".h", ".cpp", ".rb", ".json", ".yaml", ".toml", ".html",
             ".css", ".scss", ".sh", ".sql", ".md"],
    "document": [".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".rtf", ".csv", ".epub"],
    "image": [".jpg", ".png", ".gif", ".svg", ".webp", ".heic", ".raw", ".tiff"],
    "video": [".mp4", ".mkv", ".avi", ".mov", ".webm"],
    "audio": [".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a"],
    "archive": [".zip", ".tar.gz", ".7z", ".rar", ".dmg", ".iso"],
    "data": [".db", ".sqlite", ".parquet", ".pt", ".onnx", ".pkl",
             ".npy", ".h5", ".safetensors", ".ckpt"],
    "other": [".log", ".lock", ".tmp", ".bak", ".cache", "", ".cfg", ".ini"],
}

# Directory structure templates (simulates dev workstation)
DIR_TEMPLATES: list[str] = [
    "/home/dev/projects/{project}/src",
    "/home/dev/projects/{project}/src/components",
    "/home/dev/projects/{project}/src/utils",
    "/home/dev/projects/{project}/tests",
    "/home/dev/projects/{project}/docs",
    "/home/dev/projects/{project}/node_modules/{pkg}",
    "/home/dev/projects/{project}/node_modules/{pkg}/dist",
    "/home/dev/projects/{project}/.git/objects",
    "/home/dev/projects/{project}/.venv/lib/python3.12/site-packages/{pkg}",
    "/home/dev/projects/{project}/build",
    "/home/dev/projects/{project}/dist",
    "/home/dev/Downloads",
    "/home/dev/Documents",
    "/home/dev/Documents/work",
    "/home/dev/Pictures",
    "/home/dev/Pictures/screenshots",
    "/home/dev/Videos",
    "/home/dev/Music",
    "/home/dev/.docker/volumes/{volume}",
    "/home/dev/.cache/huggingface/hub/models--{model}",
    "/home/dev/.local/share/Trash/files",
]

PROJECT_NAMES = [
    "webapp", "api-server", "ml-pipeline", "data-tools", "cli-app",
    "mobile-app", "microservice", "dashboard", "auth-service", "worker",
    "frontend", "backend", "infra", "docs-site", "sdk",
]

PKG_NAMES = [
    "react", "lodash", "express", "axios", "webpack", "babel",
    "typescript", "eslint", "prettier", "jest", "numpy", "pandas",
    "scipy", "torch", "transformers", "flask", "sqlalchemy",
]

MODEL_NAMES = ["llama-2-7b", "gpt2", "bert-base", "whisper-large", "stable-diffusion"]
VOLUME_NAMES = ["postgres-data", "redis-cache", "mongo-dev", "app-uploads"]


def _weighted_category() -> str:
    """Pick a category based on configured distribution."""
    r = random.random()
    cumulative = 0.0
    for cat, weight in CATEGORY_DISTRIBUTION.items():
        cumulative += weight
        if r <= cumulative:
            return cat
    return "other"


def _generate_size(category: str) -> int:
    """Generate a realistic file size with log-normal distribution."""
    min_size, max_size = SIZE_RANGES[category]
    # Log-normal gives realistic long-tail distribution
    import math
    log_min = math.log(max(min_size, 1))
    log_max = math.log(max_size)
    log_size = random.uniform(log_min, log_max)
    # Bias toward smaller files (most files are small)
    log_size = log_min + (log_size - log_min) * random.random() ** 0.7
    return int(math.exp(log_size))


def _generate_path(category: str) -> tuple[str, str, str]:
    """Generate a realistic file path, returning (full_path, directory, filename)."""
    template = random.choice(DIR_TEMPLATES)
    path = template.format(
        project=random.choice(PROJECT_NAMES),
        pkg=random.choice(PKG_NAMES),
        model=random.choice(MODEL_NAMES),
        volume=random.choice(VOLUME_NAMES),
    )

    ext = random.choice(EXTENSIONS[category])
    base_names = ["index", "main", "utils", "config", "test", "app",
                  "data", "output", "model", "README", "package",
                  f"file_{random.randint(1, 9999)}"]
    filename = f"{random.choice(base_names)}{ext}"

    full_path = f"{path}/{filename}"
    return full_path, path, filename


def _iso_timestamp(days_ago_max: int = 730) -> str:
    """Generate a random ISO timestamp within the last N days."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    offset = datetime.timedelta(
        days=random.randint(0, days_ago_max),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    dt = now - offset
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def generate_dataset(count: int, db_path: str) -> dict:
    """Generate a synthetic dataset and write it to SQLite.

    Args:
        count: Number of file records to generate.
        db_path: Output database file path.

    Returns:
        Dict with generation stats.
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Remove existing DB
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")  # Speed optimization for bulk insert
    conn.execute("PRAGMA cache_size=-65536")

    # Create schema (minimal for benchmarks)
    conn.executescript("""
        CREATE TABLE scans (
            id TEXT PRIMARY KEY,
            root_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            scan_type TEXT NOT NULL DEFAULT 'full',
            started_at TEXT,
            completed_at TEXT,
            total_files INTEGER NOT NULL DEFAULT 0,
            total_dirs INTEGER NOT NULL DEFAULT 0,
            total_size_bytes INTEGER NOT NULL DEFAULT 0,
            files_per_second REAL,
            error_message TEXT,
            checkpoint_data TEXT,
            exclusion_patterns TEXT,
            platform TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_scans_status ON scans(status);

        CREATE TABLE files (
            id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            path TEXT NOT NULL,
            directory TEXT NOT NULL,
            filename TEXT NOT NULL,
            extension TEXT,
            size_bytes INTEGER NOT NULL,
            mime_type TEXT,
            category TEXT,
            created_at TEXT,
            modified_at TEXT,
            accessed_at TEXT,
            owner TEXT,
            permissions TEXT,
            sha256_hash TEXT,
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            is_stale INTEGER NOT NULL DEFAULT 0,
            stale_score REAL,
            risk_level TEXT,
            discovered_at TEXT NOT NULL,
            FOREIGN KEY (scan_id) REFERENCES scans(id)
        );
        CREATE INDEX idx_files_scan_id ON files(scan_id);
        CREATE INDEX idx_files_directory ON files(directory);
        CREATE INDEX idx_files_extension ON files(extension);
        CREATE INDEX idx_files_size_bytes ON files(size_bytes);
        CREATE INDEX idx_files_category ON files(category);
        CREATE INDEX idx_files_modified_at ON files(modified_at);
        CREATE INDEX idx_files_accessed_at ON files(accessed_at);

        CREATE TABLE folders (
            id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_path TEXT,
            depth INTEGER NOT NULL DEFAULT 0,
            total_size_bytes INTEGER NOT NULL DEFAULT 0,
            file_count INTEGER NOT NULL DEFAULT 0,
            dir_count INTEGER NOT NULL DEFAULT 0,
            discovered_at TEXT NOT NULL,
            FOREIGN KEY (scan_id) REFERENCES scans(id)
        );
        CREATE INDEX idx_folders_scan_id ON folders(scan_id);
        CREATE INDEX idx_folders_path ON folders(path);
        CREATE INDEX idx_folders_size ON folders(total_size_bytes);

        CREATE TABLE storage_snapshots (
            id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            total_size_bytes INTEGER NOT NULL,
            used_size_bytes INTEGER NOT NULL,
            file_count INTEGER NOT NULL,
            dir_count INTEGER NOT NULL,
            category_breakdown TEXT NOT NULL,
            extension_breakdown TEXT,
            largest_files TEXT,
            largest_dirs TEXT,
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_snapshots_date ON storage_snapshots(snapshot_date);
    """)

    # Insert scan record
    scan_id = str(uuid.uuid4())
    now = _iso_timestamp(days_ago_max=0)
    conn.execute(
        "INSERT INTO scans (id, root_path, status, started_at, completed_at, total_files, created_at) "
        "VALUES (?, '/home/dev', 'completed', ?, ?, ?, ?)",
        (scan_id, now, now, count, now),
    )

    # Generate and insert files in batches
    print(f"Generating {count:,} file records...")
    start_time = time.time()
    batch_size = 5000
    total_bytes = 0
    category_counts: dict[str, int] = {}
    category_bytes: dict[str, int] = {}
    directories: dict[str, int] = {}  # path -> total_size

    batch = []
    for i in range(count):
        category = _weighted_category()
        size = _generate_size(category)
        full_path, directory, filename = _generate_path(category)
        ext = Path(filename).suffix.lower() or None

        batch.append((
            str(uuid.uuid4()),
            scan_id,
            full_path,
            directory,
            filename,
            ext,
            size,
            category,
            _iso_timestamp(730),  # modified_at
            _iso_timestamp(365),  # accessed_at
            now,                  # discovered_at
        ))

        total_bytes += size
        category_counts[category] = category_counts.get(category, 0) + 1
        category_bytes[category] = category_bytes.get(category, 0) + size
        directories[directory] = directories.get(directory, 0) + size

        if len(batch) >= batch_size:
            conn.executemany(
                "INSERT INTO files (id, scan_id, path, directory, filename, extension, "
                "size_bytes, category, modified_at, accessed_at, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            batch.clear()
            if (i + 1) % 50000 == 0:
                print(f"  {i + 1:>8,} / {count:,} records generated...")

    # Flush remaining
    if batch:
        conn.executemany(
            "INSERT INTO files (id, scan_id, path, directory, filename, extension, "
            "size_bytes, category, modified_at, accessed_at, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )

    # Generate folder records from accumulated directories
    dir_records = []
    for dir_path, dir_size in directories.items():
        parts = dir_path.strip("/").split("/")
        parent = "/".join([""] + parts[:-1]) if len(parts) > 1 else None
        dir_records.append((
            str(uuid.uuid4()),
            scan_id,
            dir_path,
            parts[-1],
            parent,
            len(parts) - 1,
            dir_size,
            0,  # file_count populated below
            now,
        ))

    conn.executemany(
        "INSERT INTO folders (id, scan_id, path, name, parent_path, depth, "
        "total_size_bytes, file_count, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        dir_records,
    )

    # Update scan totals
    conn.execute(
        "UPDATE scans SET total_files = ?, total_dirs = ?, total_size_bytes = ? WHERE id = ?",
        (count, len(directories), total_bytes, scan_id),
    )

    conn.commit()
    elapsed = time.time() - start_time

    # Compute DB size
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db_size = os.path.getsize(db_path)

    conn.close()

    stats = {
        "file_count": count,
        "dir_count": len(directories),
        "total_bytes": total_bytes,
        "generation_seconds": round(elapsed, 2),
        "records_per_second": round(count / elapsed, 0),
        "db_size_bytes": db_size,
        "db_size_mb": round(db_size / 1024 / 1024, 1),
        "scan_id": scan_id,
        "category_distribution": category_counts,
        "category_bytes": category_bytes,
    }

    print(f"\nGenerated {count:,} files in {elapsed:.1f}s ({count/elapsed:.0f} rec/s)")
    print(f"DB size: {db_size/1024/1024:.1f} MB")
    print(f"Total simulated data: {total_bytes/1024/1024/1024:.1f} GB")
    print(f"Directories: {len(directories):,}")
    print(f"\nCategory breakdown:")
    for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
        pct = cnt / count * 100
        size_gb = category_bytes[cat] / 1024 / 1024 / 1024
        print(f"  {cat:12s}: {cnt:>8,} files ({pct:4.1f}%) — {size_gb:.1f} GB")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic benchmark dataset")
    parser.add_argument("--count", type=int, default=10000, help="Number of file records")
    parser.add_argument("--output", type=str, default="./benchmarks/data/bench.db",
                        help="Output database path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    random.seed(args.seed)
    generate_dataset(args.count, args.output)


if __name__ == "__main__":
    main()

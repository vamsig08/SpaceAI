"""SpaceAI Filesystem Scanner Engine.

Provides high-performance directory traversal with:
- Multi-threaded metadata collection via ThreadPoolExecutor
- Batched database inserts (1000 records/batch)
- Checkpoint-based crash recovery
- Configurable exclusion rules
- Cross-platform path normalization
- Symlink cycle detection
"""

from app.scanner.batch_writer import BatchWriter
from app.scanner.crawler import run_scan
from app.scanner.exclusions import ExclusionEngine, ExclusionPattern
from app.scanner.file_info import DirInfo, FileInfo, categorize_extension

__all__ = [
    "BatchWriter",
    "DirInfo",
    "ExclusionEngine",
    "ExclusionPattern",
    "FileInfo",
    "categorize_extension",
    "run_scan",
]

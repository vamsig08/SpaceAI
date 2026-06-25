"""Batched database writer for high-throughput scan inserts.

Accumulates FileInfo objects and flushes them to the database in batches
to minimize SQLite lock contention and maximize write throughput.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utc_now
from app.scanner.file_info import DirInfo, FileInfo

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class BatchWriter:
    """Accumulates file/folder records and flushes in configurable batch sizes.

    Designed for the scanner hot path:
    - Uses raw SQL INSERT for maximum throughput (bypasses ORM overhead)
    - Flushes at configurable intervals
    - Tracks total bytes and counts for progress reporting
    - Expires SQLAlchemy identity map after each flush to prevent memory growth

    Args:
        session_factory: Async session factory for creating write sessions.
        scan_id: The scan these records belong to.
        batch_size: Number of records to accumulate before flushing.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        scan_id: str,
        batch_size: int = 1000,
    ) -> None:
        self._session_factory = session_factory
        self._scan_id = scan_id
        self._batch_size = batch_size
        self._file_buffer: list[FileInfo] = []
        self._dir_buffer: list[DirInfo] = []
        self._total_files_written: int = 0
        self._total_dirs_written: int = 0
        self._total_bytes_written: int = 0

    @property
    def total_files_written(self) -> int:
        """Total file records committed to database."""
        return self._total_files_written

    @property
    def total_dirs_written(self) -> int:
        """Total directory records committed to database."""
        return self._total_dirs_written

    @property
    def total_bytes_written(self) -> int:
        """Cumulative size in bytes of all written file records."""
        return self._total_bytes_written

    @property
    def pending_count(self) -> int:
        """Number of records in buffer awaiting flush."""
        return len(self._file_buffer)

    def add_file(self, file_info: FileInfo) -> bool:
        """Add a file record to the buffer.

        Args:
            file_info: Collected file metadata.

        Returns:
            True if the buffer is full and needs flushing.
        """
        self._file_buffer.append(file_info)
        return len(self._file_buffer) >= self._batch_size

    def add_directory(self, dir_info: DirInfo) -> None:
        """Add a directory record to the buffer.

        Args:
            dir_info: Collected directory metadata.
        """
        self._dir_buffer.append(dir_info)

    async def flush(self) -> int:
        """Flush all buffered records to the database.

        Uses raw SQL multi-row INSERT for maximum throughput.
        Creates a new session per flush to avoid long-lived transactions.

        Returns:
            Number of file records flushed in this batch.
        """
        files_to_write = self._file_buffer[:]
        dirs_to_write = self._dir_buffer[:]
        self._file_buffer.clear()
        self._dir_buffer.clear()

        if not files_to_write and not dirs_to_write:
            return 0

        flushed_count = 0
        now = utc_now()

        async with self._session_factory() as session:
            try:
                # Flush files
                if files_to_write:
                    file_rows = [
                        {
                            "id": str(uuid.uuid4()),
                            "scan_id": self._scan_id,
                            "path": f.path,
                            "directory": f.directory,
                            "filename": f.filename,
                            "extension": f.extension,
                            "size_bytes": f.size_bytes,
                            "category": f.category,
                            "created_at": f.created_at,
                            "modified_at": f.modified_at,
                            "accessed_at": f.accessed_at,
                            "owner": f.owner,
                            "permissions": f.permissions,
                            "discovered_at": now,
                        }
                        for f in files_to_write
                    ]

                    await session.execute(
                        text(
                            """
                            INSERT INTO files (
                                id, scan_id, path, directory, filename, extension,
                                size_bytes, category, created_at, modified_at, accessed_at,
                                owner, permissions, discovered_at
                            ) VALUES (
                                :id, :scan_id, :path, :directory, :filename, :extension,
                                :size_bytes, :category, :created_at, :modified_at, :accessed_at,
                                :owner, :permissions, :discovered_at
                            )
                            """
                        ),
                        file_rows,
                    )
                    flushed_count = len(file_rows)
                    self._total_files_written += flushed_count
                    self._total_bytes_written += sum(f.size_bytes for f in files_to_write)

                # Flush directories
                if dirs_to_write:
                    dir_rows = [
                        {
                            "id": str(uuid.uuid4()),
                            "scan_id": self._scan_id,
                            "path": d.path,
                            "name": d.name,
                            "parent_path": d.parent_path,
                            "depth": d.depth,
                            "discovered_at": now,
                        }
                        for d in dirs_to_write
                    ]

                    await session.execute(
                        text(
                            """
                            INSERT INTO folders (
                                id, scan_id, path, name, parent_path, depth, discovered_at
                            ) VALUES (
                                :id, :scan_id, :path, :name, :parent_path, :depth, :discovered_at
                            )
                            """
                        ),
                        dir_rows,
                    )
                    self._total_dirs_written += len(dir_rows)

                await session.commit()

            except Exception:
                await session.rollback()
                logger.error(
                    "batch_write_failed",
                    scan_id=self._scan_id,
                    file_count=len(files_to_write),
                    dir_count=len(dirs_to_write),
                )
                raise

        logger.debug(
            "batch_flushed",
            scan_id=self._scan_id,
            files_flushed=flushed_count,
            dirs_flushed=len(dirs_to_write),
            total_files=self._total_files_written,
        )
        return flushed_count

    async def flush_remaining(self) -> int:
        """Flush any remaining records in the buffer. Call at scan completion.

        Returns:
            Number of file records flushed.
        """
        if self._file_buffer or self._dir_buffer:
            return await self.flush()
        return 0

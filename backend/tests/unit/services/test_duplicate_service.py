"""Unit tests for DuplicateService and the background detection pipeline."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import NotFoundError
from app.models.base import utc_now, generate_uuid
from app.services.duplicate_service import DuplicateService


async def _insert_duplicate_group(
    session: AsyncSession,
    scan_id: str,
    hash_val: str,
    file_size: int,
    file_paths: list[str],
) -> str:
    """Helper to insert a duplicate group with members."""
    group_id = generate_uuid()
    now = utc_now()
    wasted = file_size * (len(file_paths) - 1)

    await session.execute(
        text(
            """
            INSERT INTO duplicate_groups (id, scan_id, sha256_hash, file_size_bytes,
                member_count, wasted_bytes, status, created_at)
            VALUES (:id, :scan_id, :hash, :size, :count, :wasted, 'unresolved', :now)
            """
        ),
        {
            "id": group_id,
            "scan_id": scan_id,
            "hash": hash_val,
            "size": file_size,
            "count": len(file_paths),
            "wasted": wasted,
            "now": now,
        },
    )

    file_ids = []
    for path in file_paths:
        file_id = generate_uuid()
        file_ids.append(file_id)
        # Insert a file record
        await session.execute(
            text(
                """
                INSERT INTO files (id, scan_id, path, directory, filename, extension,
                    size_bytes, category, discovered_at, sha256_hash)
                VALUES (:id, :scan_id, :path, :dir, :name, '.bin', :size, 'other', :now, :hash)
                """
            ),
            {
                "id": file_id,
                "scan_id": scan_id,
                "path": path,
                "dir": "/".join(path.split("/")[:-1]),
                "name": path.split("/")[-1],
                "size": file_size,
                "now": now,
                "hash": hash_val,
            },
        )

        # Insert member
        await session.execute(
            text(
                """
                INSERT INTO duplicate_members (id, group_id, file_id, path, is_keeper, created_at)
                VALUES (:id, :gid, :fid, :path, 0, :now)
                """
            ),
            {
                "id": generate_uuid(),
                "gid": group_id,
                "fid": file_id,
                "path": path,
                "now": now,
            },
        )

    await session.commit()
    return group_id


class TestDuplicateServiceSummary:
    """Tests for get_summary."""

    async def test_returns_zeros_when_no_duplicates(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        service = DuplicateService(session)
        summary = await service.get_summary(sample_scan["id"])

        assert summary["total_groups"] == 0
        assert summary["total_duplicate_files"] == 0
        assert summary["total_wasted_bytes"] == 0

    async def test_returns_correct_totals(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        # Create 2 duplicate groups
        await _insert_duplicate_group(
            session, sample_scan["id"], "hash_aaa", 1000,
            ["/a/file1.bin", "/b/file1.bin", "/c/file1.bin"],
        )
        await _insert_duplicate_group(
            session, sample_scan["id"], "hash_bbb", 5000,
            ["/x/big.bin", "/y/big.bin"],
        )

        service = DuplicateService(session)
        summary = await service.get_summary(sample_scan["id"])

        assert summary["total_groups"] == 2
        assert summary["total_duplicate_files"] == 5  # 3 + 2
        assert summary["total_wasted_bytes"] == 7000  # 1000*2 + 5000*1


class TestDuplicateServiceListGroups:
    """Tests for list_groups."""

    async def test_lists_groups_ordered_by_wasted_desc(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        await _insert_duplicate_group(
            session, sample_scan["id"], "small", 100,
            ["/a.bin", "/b.bin"],
        )
        await _insert_duplicate_group(
            session, sample_scan["id"], "large", 50000,
            ["/x.bin", "/y.bin", "/z.bin"],
        )

        service = DuplicateService(session)
        result = await service.list_groups(sample_scan["id"])

        groups = result["groups"]
        assert len(groups) == 2
        assert groups[0]["wasted_bytes"] > groups[1]["wasted_bytes"]

    async def test_pagination_metadata(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        for i in range(5):
            await _insert_duplicate_group(
                session, sample_scan["id"], f"hash_{i}", 1000 * (i + 1),
                [f"/a/{i}.bin", f"/b/{i}.bin"],
            )

        service = DuplicateService(session)
        result = await service.list_groups(sample_scan["id"], page=1, page_size=2)

        assert len(result["groups"]) == 2
        assert result["meta"]["total_items"] == 5
        assert result["meta"]["total_pages"] == 3


class TestDuplicateServiceGroupDetail:
    """Tests for get_group_detail."""

    async def test_returns_group_with_members(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        group_id = await _insert_duplicate_group(
            session, sample_scan["id"], "detailhash", 2000,
            ["/one.bin", "/two.bin", "/three.bin"],
        )

        service = DuplicateService(session)
        detail = await service.get_group_detail(group_id)

        assert detail["id"] == group_id
        assert detail["sha256_hash"] == "detailhash"
        assert detail["member_count"] == 3
        assert len(detail["members"]) == 3

    async def test_raises_not_found(self, session: AsyncSession) -> None:
        service = DuplicateService(session)
        with pytest.raises(NotFoundError):
            await service.get_group_detail("nonexistent-id")


class TestDuplicateServiceResolve:
    """Tests for resolve_group."""

    async def test_marks_keeper_and_resolves(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        group_id = await _insert_duplicate_group(
            session, sample_scan["id"], "reshash", 3000,
            ["/keep.bin", "/remove.bin"],
        )

        # Get the file_id of the first member
        result = await session.execute(
            text("SELECT file_id FROM duplicate_members WHERE group_id = :gid LIMIT 1"),
            {"gid": group_id},
        )
        keeper_file_id = result.scalar_one()

        service = DuplicateService(session)
        res = await service.resolve_group(group_id, keeper_file_id)

        assert res["status"] == "resolved"
        assert res["files_to_cleanup"] == 1
        assert res["recoverable_bytes"] == 3000

    async def test_raises_not_found_for_invalid_group(
        self, session: AsyncSession
    ) -> None:
        service = DuplicateService(session)
        with pytest.raises(NotFoundError):
            await service.resolve_group("bad-group", "bad-file")

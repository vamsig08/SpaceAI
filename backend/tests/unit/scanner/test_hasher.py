"""Unit tests for the streaming hasher module."""

import os
from pathlib import Path

import pytest

from app.scanner.hasher import (
    are_files_identical,
    compute_full_hash,
    compute_hash_for_bytes,
    compute_partial_hash,
)


class TestComputePartialHash:
    """Tests for Stage 2 partial hash computation."""

    def test_small_file_hashes_entire_content(self, tmp_path: Path) -> None:
        """Files <= 4KB are fully hashed in partial mode."""
        f = tmp_path / "small.txt"
        f.write_bytes(b"hello world")

        result = compute_partial_hash(str(f), 11)
        expected = compute_hash_for_bytes(b"hello world")
        assert result == expected

    def test_medium_file_hashes_first_and_remaining(self, tmp_path: Path) -> None:
        """Files between 4KB and 8KB hash first chunk + rest."""
        data = b"A" * 5000  # 5KB
        f = tmp_path / "medium.bin"
        f.write_bytes(data)

        result = compute_partial_hash(str(f), 5000)
        assert result is not None
        assert len(result) == 64  # SHA256 hex digest

    def test_large_file_hashes_first_and_last_4kb(self, tmp_path: Path) -> None:
        """Files > 8KB hash first 4KB + last 4KB."""
        # Create file with distinct head and tail
        head = b"H" * 4096
        middle = b"M" * 10000
        tail = b"T" * 4096
        data = head + middle + tail
        f = tmp_path / "large.bin"
        f.write_bytes(data)

        result = compute_partial_hash(str(f), len(data))
        # Should hash head + tail = 8KB worth
        expected = compute_hash_for_bytes(head + tail)
        assert result == expected

    def test_identical_files_produce_same_partial_hash(self, tmp_path: Path) -> None:
        data = os.urandom(20000)
        f1 = tmp_path / "file1.bin"
        f2 = tmp_path / "file2.bin"
        f1.write_bytes(data)
        f2.write_bytes(data)

        h1 = compute_partial_hash(str(f1), len(data))
        h2 = compute_partial_hash(str(f2), len(data))
        assert h1 == h2

    def test_different_files_same_size_produce_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"A" * 10000)
        f2.write_bytes(b"B" * 10000)

        h1 = compute_partial_hash(str(f1), 10000)
        h2 = compute_partial_hash(str(f2), 10000)
        assert h1 != h2

    def test_returns_none_for_nonexistent_file(self) -> None:
        result = compute_partial_hash("/nonexistent/file.bin", 100)
        assert result is None

    def test_returns_none_for_permission_denied(self, tmp_path: Path) -> None:
        f = tmp_path / "locked.bin"
        f.write_bytes(b"secret")
        f.chmod(0o000)
        try:
            result = compute_partial_hash(str(f), 6)
            assert result is None
        finally:
            f.chmod(0o644)


class TestComputeFullHash:
    """Tests for Stage 3 full SHA256 hash computation."""

    def test_hashes_entire_file(self, tmp_path: Path) -> None:
        data = b"full content to hash"
        f = tmp_path / "full.txt"
        f.write_bytes(data)

        result = compute_full_hash(str(f))
        expected = compute_hash_for_bytes(data)
        assert result == expected

    def test_large_file_streaming(self, tmp_path: Path) -> None:
        """Verify streaming works for files larger than buffer size."""
        data = os.urandom(256 * 1024)  # 256KB
        f = tmp_path / "large.bin"
        f.write_bytes(data)

        result = compute_full_hash(str(f))
        expected = compute_hash_for_bytes(data)
        assert result == expected

    def test_identical_files_same_hash(self, tmp_path: Path) -> None:
        data = os.urandom(100000)
        f1 = tmp_path / "copy1.bin"
        f2 = tmp_path / "copy2.bin"
        f1.write_bytes(data)
        f2.write_bytes(data)

        assert compute_full_hash(str(f1)) == compute_full_hash(str(f2))

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")

        assert compute_full_hash(str(f1)) != compute_full_hash(str(f2))

    def test_returns_none_for_nonexistent(self) -> None:
        assert compute_full_hash("/nonexistent/file") is None

    def test_empty_file_produces_known_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "empty"
        f.touch()
        result = compute_full_hash(str(f))
        # SHA256 of empty input
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert result == expected


class TestComputeHashForBytes:
    """Tests for the in-memory hash utility."""

    def test_known_hash(self) -> None:
        # SHA256("hello") is well-known
        result = compute_hash_for_bytes(b"hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_empty_bytes(self) -> None:
        result = compute_hash_for_bytes(b"")
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestAreFilesIdentical:
    """Tests for byte-for-byte comparison."""

    def test_identical_files_return_true(self, tmp_path: Path) -> None:
        data = os.urandom(50000)
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(data)
        f2.write_bytes(data)

        assert are_files_identical(str(f1), str(f2)) is True

    def test_different_files_return_false(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"AAAA")
        f2.write_bytes(b"BBBB")

        assert are_files_identical(str(f1), str(f2)) is False

    def test_different_size_return_false(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"short")
        f2.write_bytes(b"longer content")

        assert are_files_identical(str(f1), str(f2)) is False

    def test_nonexistent_file_returns_false(self, tmp_path: Path) -> None:
        f1 = tmp_path / "exists.bin"
        f1.write_bytes(b"data")

        assert are_files_identical(str(f1), "/nonexistent") is False

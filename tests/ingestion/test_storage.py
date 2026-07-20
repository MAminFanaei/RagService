"""
Phase 1 tests — Storage abstraction layer.

Covers:
  - LocalStorage.save() persists bytes and returns correct path
  - LocalStorage.load() returns exactly what was saved
  - LocalStorage.exists() returns True / False correctly
  - LocalStorage.delete() removes file; second delete is a no-op
  - Path traversal attack is blocked with ValueError
  - MinIOStorage import path exists (class is importable)

All tests use a tmp_path fixture so they are hermetic and leave no
artefacts on disk.
"""

import pytest
import pytest_asyncio

from ingestion.storage import LocalStorage, MinIOStorage, StorageBackend


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def local_storage(tmp_path):
    """Fresh LocalStorage backed by a pytest temp directory."""
    return LocalStorage(str(tmp_path))


# --------------------------------------------------------------------------- #
# LocalStorage tests                                                           #
# --------------------------------------------------------------------------- #

class TestLocalStorage:

    @pytest.mark.asyncio
    async def test_save_returns_relative_path(self, local_storage):
        path = await local_storage.save(b"hello world", "docs/test.txt")
        assert path == "docs/test.txt"

    @pytest.mark.asyncio
    async def test_save_creates_intermediate_directories(self, local_storage, tmp_path):
        await local_storage.save(b"data", "a/b/c/file.bin")
        assert (tmp_path / "a" / "b" / "c" / "file.bin").exists()

    @pytest.mark.asyncio
    async def test_load_returns_saved_bytes(self, local_storage):
        payload = b"\x00\x01\x02 Persian text: \xd9\x81\xd8\xa7\xd8\xb1\xd8\xb3\xdb\x8c"
        await local_storage.save(payload, "test_load.bin")
        loaded = await local_storage.load("test_load.bin")
        assert loaded == payload

    @pytest.mark.asyncio
    async def test_exists_true_after_save(self, local_storage):
        await local_storage.save(b"x", "exists_check.txt")
        assert await local_storage.exists("exists_check.txt") is True

    @pytest.mark.asyncio
    async def test_exists_false_for_missing_file(self, local_storage):
        assert await local_storage.exists("no_such_file.txt") is False

    @pytest.mark.asyncio
    async def test_delete_removes_file(self, local_storage):
        await local_storage.save(b"bye", "to_delete.txt")
        assert await local_storage.exists("to_delete.txt") is True
        await local_storage.delete("to_delete.txt")
        assert await local_storage.exists("to_delete.txt") is False

    @pytest.mark.asyncio
    async def test_delete_is_idempotent(self, local_storage):
        """Deleting a non-existent file must not raise."""
        await local_storage.delete("ghost_file.txt")   # should not raise

    @pytest.mark.asyncio
    async def test_overwrite_existing_file(self, local_storage):
        await local_storage.save(b"version 1", "overwrite.txt")
        await local_storage.save(b"version 2", "overwrite.txt")
        data = await local_storage.load("overwrite.txt")
        assert data == b"version 2"

    @pytest.mark.asyncio
    async def test_binary_content_preserved(self, local_storage):
        binary = bytes(range(256))
        await local_storage.save(binary, "binary.bin")
        assert await local_storage.load("binary.bin") == binary

    # -- Path traversal ----------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_path_traversal_blocked_dotdot(self, local_storage):
        with pytest.raises(ValueError, match="traversal"):
            await local_storage.save(b"evil", "../../etc/passwd")

    @pytest.mark.asyncio
    async def test_path_traversal_blocked_absolute(self, local_storage):
        with pytest.raises(ValueError, match="traversal"):
            await local_storage.load("/etc/shadow")

    @pytest.mark.asyncio
    async def test_path_traversal_blocked_encoded(self, local_storage):
        # URL-decoded traversal attempt — Path.resolve() catches this too
        with pytest.raises(ValueError, match="traversal"):
            await local_storage.exists("subdir/../../secret")


# --------------------------------------------------------------------------- #
# Abstract base tests                                                          #
# --------------------------------------------------------------------------- #

class TestStorageBackendABC:

    def test_local_storage_is_storage_backend(self, tmp_path):
        storage = LocalStorage(str(tmp_path))
        assert isinstance(storage, StorageBackend)

    def test_minio_storage_is_storage_backend(self):
        """
        MinIOStorage must be a subclass of StorageBackend even without
        instantiating it (constructor requires a live MinIO server).
        """
        assert issubclass(MinIOStorage, StorageBackend)

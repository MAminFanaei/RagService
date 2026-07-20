"""
Storage abstraction layer for ingestion file persistence.

Three classes in one file (ABC + two implementations) — they are small
and always used together, so splitting would be over-engineering.

LocalStorage  → default, uses aiofiles, path-traversal protected
MinIOStorage  → fully implemented, use STORAGE_BACKEND=minio to activate
get_storage() → @lru_cache singleton, picks backend from config

Path traversal protection:
  resolve() every path and assert it stays inside base_dir before any IO.
"""

import os
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

import aiofiles
import aiofiles.os

from ingestion.config import settings


# --------------------------------------------------------------------------- #
# Abstract base                                                                #
# --------------------------------------------------------------------------- #

class StorageBackend(ABC):

    @abstractmethod
    async def save(self, data: bytes, path: str) -> str:
        """
        Persist `data` at `path` (relative to backend root).
        Returns the canonical path as stored.
        """
        ...

    @abstractmethod
    async def load(self, path: str) -> bytes:
        """Return raw bytes for the file at `path`."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete the file at `path`. No-op if already absent."""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Return True if `path` exists in the backend."""
        ...


# --------------------------------------------------------------------------- #
# Local filesystem implementation                                              #
# --------------------------------------------------------------------------- #

class LocalStorage(StorageBackend):
    """
    Stores files on the local filesystem under `base_dir`.

    Directory structure created on demand.
    Path traversal: any `path` that would escape `base_dir` after
    resolution raises ValueError before touching the filesystem.
    """

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir).resolve()

    # -- internal ----------------------------------------------------------- #

    def _safe_path(self, path: str) -> Path:
        """
        Join base_dir + path, resolve symlinks, assert result is still
        inside base_dir.  Raises ValueError on traversal attempt.
        """
        candidate = (self._base / path).resolve()
        if not str(candidate).startswith(str(self._base)):
            raise ValueError(
                f"Path traversal blocked: '{path}' resolves outside storage root."
            )
        return candidate

    # -- public API --------------------------------------------------------- #

    async def save(self, data: bytes, path: str) -> str:
        full = self._safe_path(path)
        await aiofiles.os.makedirs(str(full.parent), exist_ok=True)
        async with aiofiles.open(full, "wb") as fh:
            await fh.write(data)
        return path  # return relative path (what was given)

    async def load(self, path: str) -> bytes:
        full = self._safe_path(path)
        async with aiofiles.open(full, "rb") as fh:
            return await fh.read()

    async def delete(self, path: str) -> None:
        full = self._safe_path(path)
        try:
            await aiofiles.os.remove(str(full))
        except FileNotFoundError:
            pass  # idempotent

    async def exists(self, path: str) -> bool:
        full = self._safe_path(path)
        return await aiofiles.os.path.exists(str(full))


# --------------------------------------------------------------------------- #
# MinIO implementation                                                         #
# --------------------------------------------------------------------------- #

class MinIOStorage(StorageBackend):
    """
    Stores files in a MinIO (S3-compatible) bucket via miniopy-async.

    Fully implemented but not exercised by default tests.
    Activate with: STORAGE_BACKEND=minio in .env
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        # Import lazily so the package is optional when running local-only
        try:
            from miniopy_async import Minio  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "miniopy-async is required for MinIO storage. "
                "Install with: pip install miniopy-async"
            ) from exc

        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket

    async def _ensure_bucket(self) -> None:
        exists = await self._client.bucket_exists(self._bucket)
        if not exists:
            await self._client.make_bucket(self._bucket)

    async def save(self, data: bytes, path: str) -> str:
        await self._ensure_bucket()
        import io
        await self._client.put_object(
            self._bucket,
            path,
            io.BytesIO(data),
            length=len(data),
        )
        return path

    async def load(self, path: str) -> bytes:
        response = await self._client.get_object(self._bucket, path)
        return await response.read()

    async def delete(self, path: str) -> None:
        try:
            await self._client.remove_object(self._bucket, path)
        except Exception:
            pass  # idempotent

    async def exists(self, path: str) -> bool:
        try:
            await self._client.stat_object(self._bucket, path)
            return True
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# Factory                                                                      #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """
    Return the singleton storage backend chosen by STORAGE_BACKEND config.
    Cached — constructed once per process.
    """
    backend = settings.STORAGE_BACKEND.lower()

    if backend == "local":
        return LocalStorage(settings.LOCAL_STORAGE_BASE_DIR)

    if backend == "minio":
        if not settings.MINIO_ENDPOINT:
            raise ValueError("MINIO_ENDPOINT must be set when STORAGE_BACKEND=minio")
        return MinIOStorage(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            bucket=settings.MINIO_BUCKET,
            secure=settings.MINIO_SECURE,
        )

    raise ValueError(
        f"Unknown STORAGE_BACKEND={settings.STORAGE_BACKEND!r}. "
        "Expected 'local' or 'minio'."
    )

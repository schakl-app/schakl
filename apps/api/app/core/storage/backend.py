"""Pluggable file storage (issue #123) — the seam, and the local-volume default.

The self-host deploy model (one Compose host, CLAUDE.md §5) wants a named Docker volume:
no external dependency, survives image upgrades like ``db-data``, trivial to back up.
Google Drive (P3) and object storage (a multi-node future) implement the same three-method
protocol and drop in via ``SCHAKL_STORAGE_BACKEND`` — callers depend on the interface and a
``files`` row, never on a filesystem path.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import settings


class StorageBackend(Protocol):
    """What a consumer may do with stored bytes. Keys are opaque (``<org_id>/<file_id>``)."""

    def put(self, key: str, stream: BinaryIO) -> None: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...


class LocalVolumeStorage:
    """Files under ``SCHAKL_STORAGE_PATH`` (a named volume in Compose). Node-local by design —
    fine for the one-host deploy; a multi-node future swaps the backend, not the callers."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        # Keys are server-generated uuids, but resolve defensively anyway: a path escaping the
        # root is a programming error we refuse to act on.
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ValueError(f"storage key escapes the root: {key!r}")
        return path

    def put(self, key: str, stream: BinaryIO) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as out:
            while chunk := stream.read(1024 * 1024):
                out.write(chunk)

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class StorageUnavailableError(LookupError):
    """A row's recorded backend cannot be resolved on this instance (#190) — e.g. an ``s3``
    row after the S3 env config was removed. Callers translate this to a distinct 404
    (``errors.storage_backend_unavailable``), sibling of ``errors.file_bytes_missing``."""

    def __init__(self, backend: str) -> None:
        super().__init__(backend)
        self.backend = backend


def storage_for(backend: str) -> StorageBackend:
    """The backend for a **stored row** — reads/deletes dispatch on ``files.backend``, so
    enabling S3 affects new writes only and existing local files keep serving (#190)."""
    if backend == "local":
        return LocalVolumeStorage(settings.storage_path)
    if backend == "s3":
        from app.core.storage.s3 import S3ObjectStorage, s3_configured

        if not s3_configured():
            raise StorageUnavailableError(backend)
        return S3ObjectStorage()
    raise StorageUnavailableError(backend)


def get_storage() -> StorageBackend:
    """The backend **new writes** go to. Reads settings per call so tests can repoint it."""
    if settings.storage_backend not in ("local", "s3"):
        raise RuntimeError(f"unknown storage backend: {settings.storage_backend!r}")
    return storage_for(settings.storage_backend)

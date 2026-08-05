"""Pluggable file storage core (issue #123): backend seam + ``files`` metadata + REST."""

from app.core.storage.backend import LocalVolumeStorage, StorageBackend, get_storage
from app.core.storage.models import FileBlob, StoredFile
from app.core.storage.service import FileService

__all__ = [
    "FileBlob",
    "FileService",
    "LocalVolumeStorage",
    "StorageBackend",
    "StoredFile",
    "get_storage",
]

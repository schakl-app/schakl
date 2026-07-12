"""Pluggable file storage core (issue #123): backend seam + ``files`` metadata + REST."""

from app.core.storage.backend import LocalVolumeStorage, StorageBackend, get_storage
from app.core.storage.models import StoredFile
from app.core.storage.service import FileService

__all__ = [
    "FileService",
    "LocalVolumeStorage",
    "StorageBackend",
    "StoredFile",
    "get_storage",
]

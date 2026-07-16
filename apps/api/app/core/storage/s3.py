"""S3-compatible object storage backend (issue #190).

Instance-wide, configured via ``SCHAKL_STORAGE_S3_*`` environment variables — coded strictly
against the S3 API (endpoint + region + bucket + credentials), so Hetzner Object Storage,
MinIO, Scaleway and AWS all work. Design rules:

* **The bucket stays private; the API remains the only data path** (Golden Rule 6). Every
  byte still travels ``require_context`` — files are never served raw from the bucket.
* **Org-prefixed keys**: ``storage_key`` stays ``<org_id>/<file_id>``, so tenant isolation
  holds at the key level even in a shared bucket. An optional instance-wide key prefix nests
  under one more path segment.
* ``open()`` downloads fully into a :class:`~tempfile.SpooledTemporaryFile` (bounded by the
  upload cap) so **no S3 socket outlives the call** — response streaming never pins an S3
  connection, and callers can wrap the whole call in ``ctx.release_db()`` so it never pins a
  DB connection either (docs/PERFORMANCE.md).
* A missing object raises :class:`FileNotFoundError`, so the router's existing volume-drift
  warning + 404 applies unchanged.

boto3 is sync — called through ``asyncio.to_thread`` exactly like ``LocalVolumeStorage``.
The client is cached per credential tuple; connect/read timeouts and a low retry count make
a dead endpoint fail fast instead of freezing a request.
"""

from __future__ import annotations

import tempfile
from functools import lru_cache
from typing import BinaryIO

from app.config import settings


@lru_cache(maxsize=4)
def _client(
    endpoint: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    force_path_style: bool,
):
    """One boto3 client per credential tuple — boto3 clients are thread-safe and expensive
    to build. Lazy import keeps boto3 off the startup path for local-only instances."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region or None,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path" if force_path_style else "virtual"},
        ),
    )


def s3_configured() -> bool:
    """Whether the instance's env config is complete enough to reach a bucket."""
    return bool(
        settings.storage_s3_endpoint
        and settings.storage_s3_bucket
        and settings.storage_s3_access_key_id
        and settings.storage_s3_secret_access_key
    )


class S3ObjectStorage:
    """The three-method protocol against a private S3-compatible bucket."""

    def __init__(self) -> None:
        self._bucket = settings.storage_s3_bucket
        self._prefix = settings.storage_s3_key_prefix.strip("/")

    def _s3(self):
        return _client(
            settings.storage_s3_endpoint,
            settings.storage_s3_region,
            settings.storage_s3_access_key_id,
            settings.storage_s3_secret_access_key,
            settings.storage_s3_force_path_style,
        )

    def _key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def put(self, key: str, stream: BinaryIO) -> None:
        self._s3().upload_fileobj(stream, self._bucket, self._key(key))

    def open(self, key: str) -> BinaryIO:
        """Download fully into a spooled temp file, so the returned handle owns no socket.

        Files are bounded by the upload cap (10 MiB default), so buffering is cheap; what it
        buys is that response streaming and slow clients can never pin an S3 connection —
        and, wrapped in ``ctx.release_db()``, no DB connection either.
        """
        from botocore.exceptions import ClientError

        buffer = tempfile.SpooledTemporaryFile(max_size=settings.upload_max_bytes)
        try:
            self._s3().download_fileobj(self._bucket, self._key(key), buffer)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                buffer.close()
                raise FileNotFoundError(key) from exc
            buffer.close()
            raise
        buffer.seek(0)
        return buffer

    def delete(self, key: str) -> None:
        self._s3().delete_object(Bucket=self._bucket, Key=self._key(key))

"""Prefix deletion on both storage backends (epic #199).

Terminating an org has to reclaim the bytes it owned. Without this the DB row disappears and
the blobs stay: on a node volume that is dead weight, on object storage it is a bill for a
tenant that no longer exists. The one thing these tests really pin is the **prefix boundary** —
a plain string prefix would take ``a1b2c3`` along with ``a1b2``, and those are two tenants.
"""

from __future__ import annotations

import io

import pytest

from app.core.storage.backend import LocalVolumeStorage
from app.core.storage.s3 import S3ObjectStorage


def test_local_delete_prefix_removes_only_that_org(tmp_path) -> None:
    storage = LocalVolumeStorage(str(tmp_path))
    storage.put("a1b2/one", io.BytesIO(b"x"))
    storage.put("a1b2/two", io.BytesIO(b"y"))
    # A different org whose id merely *starts with* the first one's.
    storage.put("a1b2c3/keep", io.BytesIO(b"z"))

    assert storage.delete_prefix("a1b2") == 2
    with pytest.raises(FileNotFoundError):
        storage.open("a1b2/one")
    assert storage.open("a1b2c3/keep").read() == b"z"


def test_local_delete_prefix_is_idempotent(tmp_path) -> None:
    storage = LocalVolumeStorage(str(tmp_path))
    assert storage.delete_prefix("never-existed") == 0


def test_local_delete_prefix_refuses_to_escape_the_root(tmp_path) -> None:
    """The prefix goes through the same escape check as a single key: a caller that could pass
    ``..`` here would delete the volume rather than one org's directory."""
    storage = LocalVolumeStorage(str(tmp_path / "root"))
    with pytest.raises(ValueError):
        storage.delete_prefix("../..")


class _FakeBoto:
    """Just the two S3 calls delete_prefix makes, with pagination."""

    def __init__(self, keys: list[str], *, page_size: int = 2) -> None:
        self.keys = list(keys)
        self.page_size = page_size
        self.deleted: list[str] = []

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        outer = self

        class _Paginator:
            def paginate(self, *, Bucket: str, Prefix: str):  # noqa: N803 — boto3's own kwargs
                hits = [k for k in outer.keys if k.startswith(Prefix)]
                for start in range(0, len(hits), outer.page_size):
                    yield {"Contents": [{"Key": k} for k in hits[start : start + outer.page_size]]}

        return _Paginator()

    def delete_objects(self, *, Bucket: str, Delete: dict) -> dict:  # noqa: N803
        self.deleted.extend(item["Key"] for item in Delete["Objects"])
        return {}


def test_s3_delete_prefix_paginates_and_respects_the_boundary(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "storage_s3_bucket", "schakl")
    monkeypatch.setattr(settings, "storage_s3_key_prefix", "prod")
    storage = S3ObjectStorage()
    fake = _FakeBoto(
        [
            "prod/a1b2/one",
            "prod/a1b2/two",
            "prod/a1b2/three",
            "prod/a1b2c3/keep",  # a different org, same leading characters
            "prod/other/keep",
        ]
    )
    monkeypatch.setattr(storage, "_s3", lambda: fake)

    removed = storage.delete_prefix("a1b2")
    assert removed == 3
    assert sorted(fake.deleted) == ["prod/a1b2/one", "prod/a1b2/three", "prod/a1b2/two"]
    assert "prod/a1b2c3/keep" not in fake.deleted
    assert "prod/other/keep" not in fake.deleted


def test_s3_delete_prefix_honours_the_instance_key_prefix(monkeypatch) -> None:
    """The instance prefix is applied on delete exactly as it is on read and write — otherwise
    a terminated org's objects would be looked for at the wrong path and silently survive."""
    from app.config import settings

    monkeypatch.setattr(settings, "storage_s3_bucket", "schakl")
    monkeypatch.setattr(settings, "storage_s3_key_prefix", "")
    storage = S3ObjectStorage()
    fake = _FakeBoto(["a1b2/one", "prod/a1b2/two"])
    monkeypatch.setattr(storage, "_s3", lambda: fake)

    assert storage.delete_prefix("a1b2") == 1
    assert fake.deleted == ["a1b2/one"]

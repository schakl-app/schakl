"""``core/avatars.py``: an identity provider's picture is fetched only from a closed list of
hosts, over HTTPS — a document render must never be a way to make the API call an arbitrary
address someone put in a column."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import avatars


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://lh3.googleusercontent.com/a-/abc=s96-c", True),
        ("https://googleusercontent.com/x", True),
        ("http://lh3.googleusercontent.com/a-/abc", False),
        ("https://evilgoogleusercontent.com/x", False),
        ("https://lh3.googleusercontent.com.evil.test/x", False),
        ("https://169.254.169.254/latest/meta-data", False),
        ("file:///etc/passwd", False),
    ],
)
def test_only_idp_picture_hosts_are_fetched(url: str, allowed: bool) -> None:
    assert (avatars._allowed_host(url) is not None) is allowed


async def test_a_url_off_the_list_is_never_requested(monkeypatch) -> None:
    def _boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("fetched")

    monkeypatch.setattr(avatars.httpx, "AsyncClient", _boom)
    user = SimpleNamespace(custom_avatar_url=None, oidc_avatar_url="https://example.test/me.png")
    assert await avatars.load_user_avatar(None, user) == (None, None)

"""Pre-auth brute-force limits: the login form can't be hammered 100 times a minute.

The suite runs with the limits off (conftest), so each test that needs them switches on a low
ceiling and swaps in an in-memory fake Redis — the same shape ``test_update_check.py`` uses —
so the counter is deterministic and no live Redis is required. Fail-open behaviour (Redis
unreachable ⇒ never blocks sign-in) is asserted directly against the limiter.

The fake Redis was not enough on its own: the limiter's *window* is the wall clock, and a burst
that spends it is the one input these tests do not control. Hence ``frozen_window`` — see #417
and the fixture's own note.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import settings
from app.core.auth import emails as auth_emails
from app.core.auth import ratelimit
from tests.conftest import make_tenant

LOGIN = "/api/v1/auth/login"
FORGOT = "/api/v1/auth/forgot-password"


@pytest.fixture
def frozen_window(monkeypatch) -> None:
    """Pin the one-minute window a burst of requests is counted in.

    ``_enforce`` derives its key from ``datetime.now(UTC)``, which is exactly right in
    production — a fixed window in the shared Redis is what makes the ceiling hold across
    replicas — and is the only moving part a test spending a budget across several requests
    does not control. Each attempt costs a deliberately slow argon2 verify, so a burst occupies
    real wall-clock time on a shared runner; straddle a minute boundary and the counter restarts
    at 1, the attempt that should have been refused is an ordinary wrong-password 400, and the
    assertion reads ``400 == 429`` (#417). It is rare, and it is a red CI nobody can attribute.

    Freezing the clock rather than teaching ``_FakeRedis`` to ignore the window keeps the double
    honest about what it stands in for: the key the limiter builds — window segment and all — is
    still the key the counter is kept under, so a limiter that counted the wrong thing would
    still fail here. What is left to assert is the ceiling, which is what these tests are for.
    """
    fixed = datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)

    class _FrozenClock:
        """Stands in for the ``datetime`` *class* as ``ratelimit`` imported it."""

        @staticmethod
        def now(tz=None):  # noqa: ANN001, ANN205 - mirrors datetime.now's signature
            return fixed

    monkeypatch.setattr(ratelimit, "datetime", _FrozenClock)


class _FakeRedis:
    """Minimal INCR/EXPIRE over a dict — enough for the fixed-window limiter."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002 - TTL irrelevant in-memory
        return None


class _BrokenRedis:
    async def incr(self, key: str) -> int:
        raise ConnectionError("redis down")

    async def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002
        raise ConnectionError("redis down")


async def test_login_blocks_after_the_limit(client_for, monkeypatch, frozen_window) -> None:
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 3)
    fake = _FakeRedis()  # one instance shared across the burst, so the window accumulates
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)

    t = await make_tenant("rl-login")
    async with client_for(t.host) as c:
        # Three wrong-password attempts are allowed through (each a 400, not a 429)...
        for _ in range(3):
            r = await c.post(LOGIN, data={"username": t.user.email, "password": "wrong"})
            assert r.status_code == 400

        # ...the fourth within the same minute is refused with the standard envelope.
        blocked = await c.post(LOGIN, data={"username": t.user.email, "password": "wrong"})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"

        # And the ceiling is on attempts, not on success: even the *correct* password is now
        # refused — which is the whole point of throttling a guessing attack.
        even_correct = await c.post(
            LOGIN, data={"username": t.user.email, "password": t.password}
        )
        assert even_correct.status_code == 429


async def test_reset_has_its_own_independent_budget(
    client_for, monkeypatch, frozen_window
) -> None:
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 3)
    monkeypatch.setattr(settings, "password_reset_rate_limit_per_minute", 2)
    fake = _FakeRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)

    async def _no_email(*args, **kwargs) -> tuple[bool, str | None]:  # noqa: ANN002, ANN003
        # Honour the real contract — the manager unpacks (sent, error).
        return True, None

    monkeypatch.setattr(auth_emails, "send_password_email", _no_email)

    t = await make_tenant("rl-reset")
    async with client_for(t.host) as c:
        # Spending the whole login budget must not touch the reset budget.
        for _ in range(4):
            await c.post(LOGIN, data={"username": t.user.email, "password": "wrong"})

        # forgot-password always answers 202 (it never reveals whether the address exists),
        # until its own separate limit of 2/min is exceeded.
        assert (await c.post(FORGOT, json={"email": t.user.email})).status_code == 202
        assert (await c.post(FORGOT, json={"email": t.user.email})).status_code == 202
        blocked = await c.post(FORGOT, json={"email": t.user.email})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"


async def test_limit_of_zero_disables_it(client_for, monkeypatch) -> None:
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 0)
    fake = _FakeRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)

    t = await make_tenant("rl-off")
    async with client_for(t.host) as c:
        for _ in range(20):
            r = await c.post(LOGIN, data={"username": t.user.email, "password": "wrong"})
            assert r.status_code == 400  # never a 429; Redis is never even touched
    assert fake.counts == {}


async def test_fails_open_when_redis_is_unreachable(monkeypatch) -> None:
    """A Redis outage must never be the reason a login is blocked."""
    monkeypatch.setattr(ratelimit, "get_redis", lambda: _BrokenRedis())
    # Well past any ceiling — a broken backend simply lets every call through.
    for _ in range(50):
        await ratelimit._enforce("login:host:1.2.3.4", limit=3)

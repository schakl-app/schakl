"""Pre-auth brute-force limits: the login form can't be hammered 100 times a minute.

The suite runs with the limits off (conftest), so each test that needs them switches on a low
ceiling and swaps in an in-memory fake Redis — the same shape ``test_update_check.py`` uses —
so the counter is deterministic and no live Redis is required. Fail-open behaviour (Redis
unreachable ⇒ never blocks sign-in) is asserted directly against the limiter.
"""

from __future__ import annotations

from app.config import settings
from app.core.auth import emails as auth_emails
from app.core.auth import ratelimit
from tests.conftest import make_tenant

LOGIN = "/api/v1/auth/login"
FORGOT = "/api/v1/auth/forgot-password"


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


async def test_login_blocks_after_the_limit(client_for, monkeypatch) -> None:
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


async def test_reset_has_its_own_independent_budget(client_for, monkeypatch) -> None:
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 3)
    monkeypatch.setattr(settings, "password_reset_rate_limit_per_minute", 2)
    fake = _FakeRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)

    async def _no_email(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

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

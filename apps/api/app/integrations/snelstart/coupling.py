"""The unified coupling broker: how a granted koppelsleutel reaches the right tenant (#377).

Business-licensed — see LICENSE.

SnelStart's activation flow is the difference between an agency copying a 700-character key out
of one browser tab into another and pressing one button. It works like this:

1. A certified partner registers **one** webhook URL and receives an ``appShortName``.
2. The tenant is sent to ``web.snelstart.nl/couplings/activate/{shortname}?referenceKey=…``.
3. They approve, and SnelStart POSTs ``{KoppelSleutel, ActionType, ReferenceKey}`` to that one
   URL. **There is no retry.**

One URL for every tenant is precisely why the cloud posture needs a broker and self-hosted does
not. On cloud the callback lands on the instance apex — a host where *no org resolves* — so the
request has to carry its own tenancy, which is what the ``referenceKey`` is for: it is
``{org}.{account}.{secret}``, the same shape ``app.core.payments.tokens`` uses for a payment
provider's callback, chosen because the problem is identical and getting it wrong is a
cross-tenant write. On a self-hosted box the hostname is one SnelStart has never heard of and
could not post to, so the activation button does not render at all and connecting is a paste.

The security order is the payment webhook's, for the same reasons, with one addition that is
specific to a credential: **the body is a hint, never a fact**. It claims to carry a
koppelsleutel; we believe it only after minting a token with it and reading ``/companyInfo``.
That is not paranoia about SnelStart — it is what turns "somebody posted a plausible payload"
into "this key opens these books", which is the only thing worth storing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.crypto import encrypt
from app.core.models import Org, OrgStatus
from app.db import async_session_maker, set_current_org
from app.integrations.snelstart.client import SnelstartClient, SnelstartError
from app.integrations.snelstart.models import (
    SnelstartAccount,
    SnelstartAccountStatus,
    SnelstartConnectMethod,
)
from app.integrations.snelstart.service import (
    new_secret,
    parse_coupling_reference,
    subscription_key_for,
)

logger = logging.getLogger("schakl.snelstart")

#: A koppelsleutel is a few hundred characters. Anything an order of magnitude past that is not
#: one, and refusing before parsing keeps a hostile POST from costing anything (§17: every cap
#: is checked *before* the work it bounds).
MAX_BODY_BYTES = 64 * 1024

ACTION_CREATE = "create"
ACTION_REGENERATE = "regenerate"
ACTION_DELETE = "delete"


async def handle_coupling_callback(body: bytes) -> int:
    """Process one coupling POST and return the status to answer with.

    Split out of the router so the security order is written once, in a function that can be
    tested without a transport.

    **Almost everything answers 200**, and that is a decision rather than laziness: SnelStart
    treats any 2xx as delivered and *does not retry*, so a 4xx buys nothing except a tenant
    watching a connect flow fail silently. The exception is a failure we might recover from,
    which answers 503 — not because SnelStart will try again (it will not) but because that is
    the honest status, and the tenant can always press activate a second time.
    """
    if len(body) > MAX_BODY_BYTES:
        logger.warning("snelstart: coupling callback body too large (%d bytes)", len(body))
        return 200
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, ValueError):
        logger.warning("snelstart: coupling callback was not JSON")
        return 200
    if not isinstance(payload, dict):
        return 200

    reference = _field(payload, "ReferenceKey")
    parsed = parse_coupling_reference(reference)
    if parsed is None:
        # A malformed reference and a wrong secret must look identical from outside.
        logger.info("snelstart: coupling callback with an unusable referenceKey")
        return 200
    org_id, account_id, secret = parsed

    action = (_field(payload, "ActionType") or "").strip().lower()
    client_key = _field(payload, "KoppelSleutel")

    async with async_session_maker() as session:
        org = await session.get(Org, org_id)
        if org is None or org.status != OrgStatus.ACTIVE.value:
            return 200
        # Bound before anything is read, so every read below is org-scoped and fails closed.
        await set_current_org(session, org.id)
        account = await session.get(SnelstartAccount, account_id)
        if account is None or account.org_id != org.id:
            return 200
        if not _matches(account.connect_secret, secret):
            # A bare 200 with nothing done. Never a 401 or 403, which would confirm the account.
            logger.info("snelstart: coupling callback secret mismatch for org %s", org.slug)
            return 200

        if action == ACTION_DELETE:
            _disconnect(account)
            await session.commit()
            logger.info("snelstart: coupling revoked for org %s", org.slug)
            return 200

        if not client_key:
            logger.warning("snelstart: coupling callback carried no key for org %s", org.slug)
            return 200

        # Gate 4: the body is a hint. Prove the key before storing it — and learn which
        # administration it opens while we are there, since that is the fact a tenant actually
        # needs on screen and a key that "works" can still be the wrong books.
        try:
            probe = SnelstartClient(
                client_key=client_key, subscription_key=subscription_key_for(account)
            )
            info = await probe.company_info()
            scopes = list(probe.scopes)
        except SnelstartError as exc:
            # The tenant approved a coupling and the key does not work: worth recording on the
            # row so the settings screen says so, rather than leaving it pending for ever with
            # nothing to explain why.
            account.status = SnelstartAccountStatus.ERROR.value
            account.last_error = str(exc)[:500]
            await session.commit()
            logger.warning("snelstart: granted key failed its probe for org %s", org.slug)
            return 503
        except Exception:  # noqa: BLE001 — a missing subscription key, a decrypt failure…
            logger.exception("snelstart: coupling probe failed for org %s", org.slug)
            return 503

        _adopt(account, client_key, info, scopes)
        await session.commit()
        logger.info(
            "snelstart: coupling %s for org %s → %s",
            action or ACTION_CREATE,
            org.slug,
            account.administration_name,
        )
        return 200


def _adopt(
    account: SnelstartAccount, client_key: str, info: dict[str, Any], scopes: list[str]
) -> None:
    """Store a proven key and everything it just told us about the administration.

    The connect secret is **not** rotated here. It is the address SnelStart posts to, and
    SnelStart posts again for a ``Regenerate`` — rotating it on receipt would make the next
    regeneration undeliverable, which is the shape of bug that only appears months later when
    somebody re-issues a key.
    """
    from datetime import UTC, datetime

    account.client_key_encrypted = encrypt(client_key)
    account.connect_method = SnelstartConnectMethod.COUPLING.value
    account.company_info = info
    account.administration_name = str(info.get("administratieNaam") or "")[:255] or None
    account.article_code_kind = str(info.get("artikelcodeSoort") or "") or None
    length = info.get("artikelcodeMaxLengte")
    account.article_code_max_length = int(length) if isinstance(length, int) else None
    account.scopes = scopes
    account.status = SnelstartAccountStatus.ACTIVE.value
    account.last_error = None
    account.last_verified_at = datetime.now(UTC)

    identifier = info.get("administratieIdentifier")
    try:
        import uuid as _uuid

        account.administration_id = _uuid.UUID(str(identifier))
    except (ValueError, TypeError):
        account.administration_id = None


def _disconnect(account: SnelstartAccount) -> None:
    """SnelStart revoked the coupling: forget the key, keep the record.

    The links, the mappings and the run history are the tenant's own record of what was pushed
    into their books, and a revoked key is not a reason to destroy the audit trail of a ledger.
    The account reverts to ``pending``, which is exactly what it is: connectable, not connected.
    A fresh secret goes on, because the old address belonged to a coupling that no longer
    exists.
    """
    account.client_key_encrypted = None
    account.status = SnelstartAccountStatus.PENDING.value
    account.last_error = None
    account.scopes = []
    account.connect_secret = new_secret()


def _field(payload: dict[str, Any], name: str) -> str:
    """One field, case-insensitively.

    SnelStart documents ``KoppelSleutel``/``ActionType``/``ReferenceKey`` in PascalCase, and a
    .NET serialiser is one configuration line away from camelCase. Matching either costs
    nothing and is the difference between working and a silent no-op nobody can debug from
    outside.
    """
    for key, value in payload.items():
        if isinstance(key, str) and key.lower() == name.lower():
            return str(value or "").strip()
    return ""


def _matches(stored: str, given: str) -> bool:
    """Constant-time comparison of the reference's secret half."""
    import hmac

    if not stored or not given:
        return False
    return hmac.compare_digest(stored, given)

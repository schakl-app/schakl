"""Tag Manager failures, classified. Business-licensed — see LICENSE.

GTM answers like the rest of Google and unlike Google Ads: an ordinary ``{"error": {"code",
"status", "message", "details": [{"reason"}]}}`` body, which :func:`app.integrations.google.client
.describe_api_error` already reads. What this module adds is the *classification*, because the
status code alone is not the diagnosis:

* **403** is four different sentences with four different people who can fix them — the Tag
  Manager API is switched off in the Cloud project (nothing in-app fixes it), the token was
  minted before this org asked for the GTM scopes (a reconnect fixes it), the Google account
  simply has no access to that container (the client's GTM admin fixes it), or the container is
  read-only to this user (``publish`` on a grant that only carries ``edit.containers``).
* **409** is the one that matters most here and has no analogue in the other integrations: a
  **fingerprint mismatch**. GTM's optimistic concurrency means "somebody edited this tag in the
  Tag Manager interface since you read it", which is an ordinary Tuesday at an agency and must
  read as *"open it again"* rather than as an outage.
* **429** is a rate, not a verdict, and is the only refusal worth retrying.

Two rules are the same as everywhere else. The envelope carries an **i18n key**, never Google's
text (§9); Google's own sentence goes on the container row's ``last_error`` where an admin reads
it. And nothing that leaves here carries a credential — :func:`app.core.googleads.errors.scrub` is
imported rather than copied: it is core, it is not Ads-specific (it redacts refresh tokens, access
tokens, client secrets and client ids by *shape*), and a second copy is a second thing nobody
updates when Google mints a new credential format.
"""

from __future__ import annotations

from typing import Any

import httpx

# Core, not another module (§6): the patterns describe Google credentials, not Ads ones.
from app.core.googleads.errors import scrub
from app.errors import AppError

#: Google's machine-readable reason for "this API is not enabled in the Cloud project". Nothing
#: the user can do in-app fixes it: reconnecting mints the same token against the same project.
_API_DISABLED_REASONS = frozenset({"SERVICE_DISABLED", "accessNotConfigured"})
#: The bearer is valid and was minted without the scope this call needs — that *is* a reconnect.
_SCOPE_REASONS = frozenset(
    {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions", "insufficientScopes"}
)


class GtmError(AppError):
    """A Tag Manager call failed. ``str(exc)`` is Google's own text, already scrubbed.

    It is an :class:`~app.errors.AppError` for the reason ``AdsError`` is one: every route that
    reaches Google then surfaces the right status and i18n key without remembering to catch
    anything, and the route that forgets is not a 500 with Google's sentence in the log.
    """

    code = "gtm_error"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str | None = None,
    ) -> None:
        Exception.__init__(self, message)
        self.code = type(self).code
        self.message_key = f"errors.{type(self).code}"
        self.status_code = type(self).status_code
        self.fields: dict[str, str] | None = None
        #: The HTTP status Google answered with, or ``None`` for a transport failure.
        self.status = status
        #: ``error.details[].reason`` — the only reliable way to tell two 403s apart.
        self.reason = reason


class GtmNotConfigured(GtmError):
    """No linked container, no Google connection, or a grant without the GTM scopes.

    **A presentable state, not a bug** — the picker says so, the panel says so, the nightly sync
    records it and moves on. Raised rather than returned as ``None`` for the reason
    :class:`~app.core.googleads.errors.AdsNotConfigured` gives: a ``None`` container path reaches
    the URL builder and asks Google about a container called "None", which comes back 404 and
    reads as "somebody deleted the client's container".
    """

    code = "gtm_not_configured"
    status_code = 409


class GtmAuthError(GtmError):
    """401 — the grant is dead. The owner reconnects their Google account."""

    code = "gtm_auth"
    status_code = 502


class GtmScopeError(GtmError):
    """403 with a scope reason — the connection predates the GTM consent. Reconnect."""

    code = "gtm_scope"
    status_code = 502


class GtmApiDisabledError(GtmError):
    """403 — the Tag Manager API is not enabled in this org's Cloud project."""

    code = "gtm_api_disabled"
    status_code = 502


class GtmPermissionError(GtmError):
    """403 — this Google account has no (or not enough) access to that container."""

    code = "gtm_permission"
    status_code = 502


class GtmNotFoundError(GtmError):
    """404 — the account, container, workspace or resource is gone at Google's end."""

    code = "gtm_not_found"
    status_code = 502


class GtmConflictError(GtmError):
    """409 — a fingerprint mismatch: somebody changed this in Tag Manager since we read it."""

    code = "gtm_conflict"
    status_code = 409


class GtmInvalidError(GtmError):
    """400 — GTM refused the payload. Its message names the parameter, so it is worth keeping.

    This is the failure a hand-written tag body produces, and it is *loud* on purpose: GTM
    validates parameter keys against the tag template, so ``measurementId`` where the GA4 event
    tag wants ``measurementIdOverride`` comes back as a refusal rather than as a tag that quietly
    measures nothing.
    """

    code = "gtm_invalid"
    status_code = 422


class GtmQuotaError(GtmError):
    """429 / RESOURCE_EXHAUSTED — a rate, not a verdict. The one refusal worth waiting out."""

    code = "gtm_quota"
    status_code = 429


class GtmUnavailable(GtmError):
    """A timeout, a connection failure, or a 5xx. Retryable."""

    code = "gtm_unreachable"
    status_code = 502


def classify(payload: dict[str, Any] | None, *, status: int, fallback: str) -> GtmError:
    """Google's refusal, as the exception that names what to do about it."""
    error = (payload or {}).get("error")
    if not isinstance(error, dict):
        error = {}
    message = scrub(str(error.get("message") or fallback or f"HTTP {status}"))
    reason: str | None = None
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reason = str(detail["reason"])
            break
    if reason is None:
        for legacy in error.get("errors") or []:  # the older Google JSON error shape
            if isinstance(legacy, dict) and legacy.get("reason"):
                reason = str(legacy["reason"])
                break

    if status == 400:
        return GtmInvalidError(message, status=status, reason=reason)
    if status == 401:
        return GtmAuthError(message, status=status, reason=reason)
    if status == 403:
        if reason in _API_DISABLED_REASONS:
            return GtmApiDisabledError(message, status=status, reason=reason)
        if reason in _SCOPE_REASONS:
            return GtmScopeError(message, status=status, reason=reason)
        if str(error.get("status") or "") == "RESOURCE_EXHAUSTED" or reason in {
            "rateLimitExceeded",
            "userRateLimitExceeded",
        }:
            # Google's older APIs answer a rate limit as 403 with a quota reason. Reading it as
            # "no access" sends an agency to check permissions that were never the problem.
            return GtmQuotaError(message, status=status, reason=reason)
        return GtmPermissionError(message, status=status, reason=reason)
    if status == 404:
        return GtmNotFoundError(message, status=status, reason=reason)
    if status == 409:
        return GtmConflictError(message, status=status, reason=reason)
    if status == 429:
        return GtmQuotaError(message, status=status, reason=reason)
    if status >= 500:
        return GtmUnavailable(message, status=status, reason=reason)
    return GtmError(message, status=status, reason=reason)


def describe_failure(exc: Exception) -> str:
    """One line for a container row's ``last_error``: what Google said, scrubbed and capped."""
    if isinstance(exc, GtmError):
        head = f"{exc.status} " if exc.status else ""
        tail = f" ({exc.reason})" if exc.reason else ""
        return f"{head}{exc}{tail}"[:500]
    if isinstance(exc, httpx.HTTPError):
        return scrub(str(exc))[:500]
    return scrub(str(exc))[:500]

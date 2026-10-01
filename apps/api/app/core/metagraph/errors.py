"""Meta failures, classified — and scrubbed before anything leaves this process.

Meta answers every refusal in one envelope, whatever the HTTP status::

    {"error": {"message": "…", "type": "OAuthException", "code": 190, "error_subcode": 463,
               "error_user_title": "…", "error_user_msg": "…", "fbtrace_id": "…",
               "error_data": {"blame_field_specs": [["daily_budget"]]}}}

The status is nearly useless as a diagnosis — a dead token, a missing permission and a field the
Marketing API rejected all arrive as ``400`` — so the classification reads ``code`` and
``error_subcode``, and only falls back to the status where there is no body to read.

Three rules are the ones every integration here keeps:

* **The envelope carries an i18n key, never Meta's text** (CLAUDE.md §9). ``str(exc)`` stays
  Meta's own sentence, scrubbed, for a row's ``last_error`` and the activity trail, where a
  person can act on it. What rides the envelope's ``details`` are identifiers: the code, the
  subcode, the trace id Meta's support asks for, and the *field names* a validation refusal
  blamed — which is what lets an agent correct an ad set without a second call.
* **Nothing that leaves here contains a credential.** A Meta token has a recognisable shape
  (``EAA…``) and an app access token is ``{app-id}|{app-secret}``; both are redacted by
  pattern, and the token and secret in use are redacted by value.
* **A rate is not a verdict.** Codes 4, 17, 32, 613 and 80000–80014 are limits; the client
  reads how long to wait off the usage header and the error says so in ``details``.
"""

from __future__ import annotations

import re
from typing import Any

from app.errors import AppError

REDACTED = "[redacted]"

#: Credential shapes redacted from every outgoing string: a Graph access token, an app access
#: token (``1234567890|abcdef…``), and the two query parameters a URL may carry them in.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bEAA[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b[0-9]{8,}\|[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\b(access_token|client_secret|fb_exchange_token|appsecret_proof)=[^&\s\"']+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.|]{10,}"),
)


def scrub(text: str, *extra: str | None) -> str:
    """``text`` with every known credential shape — and every value in ``extra`` — redacted."""
    out = text
    for value in extra:
        if value and len(value) >= 8:
            out = out.replace(value, REDACTED)
    for pattern in _PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


class MetaError(AppError):
    """A Meta call failed. ``str(exc)`` is Meta's own text, already scrubbed.

    An :class:`~app.errors.AppError` for the reason ``AdsError`` is one: every route that reaches
    Meta then surfaces the right status and i18n key without remembering to catch anything. It
    is built field by field because ``AppError.__init__`` would overwrite ``str(exc)`` with the
    message key, and the sentence is what a row's ``last_error`` needs.
    """

    code = "meta_error"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        meta_code: int | None = None,
        subcode: int | None = None,
        trace_id: str | None = None,
        blame_fields: tuple[str, ...] = (),
        retry_after_minutes: int | None = None,
    ) -> None:
        Exception.__init__(self, message)
        self.code = type(self).code
        self.message_key = f"errors.{type(self).code}"
        self.status_code = type(self).status_code
        self.fields: dict[str, str] | None = None
        self.details: dict[str, Any] | None = {
            key: value
            for key, value in (
                ("meta_code", meta_code),
                ("meta_subcode", subcode),
                ("meta_trace_id", trace_id),
                ("meta_fields", list(blame_fields) or None),
                ("retry_after_minutes", retry_after_minutes),
            )
            if value is not None
        } or None
        #: The HTTP status Meta answered with, or ``None`` for a transport failure.
        self.status = status
        self.meta_code = meta_code
        self.subcode = subcode
        self.trace_id = trace_id
        self.blame_fields = blame_fields
        self.retry_after_minutes = retry_after_minutes


class MetaNotConfigured(MetaError):
    """No app, no credential, or no linked asset. A presentable state, not a bug.

    Raised rather than returned as ``None``: a ``None`` page id reaches the URL builder and asks
    Meta about a Page called "None" (``AdsNotConfigured``'s reason, one vendor over).
    """

    code = "meta_not_configured"
    status_code = 409


class MetaAuthError(MetaError):
    """Code 190 — the token is expired, revoked or otherwise dead. Only a new token fixes it."""

    code = "meta_auth"
    status_code = 409


class MetaAppSecretError(MetaError):
    """The app id or secret was refused. **Not** the token's fault, and a different person's fix.

    A refresh rejected on ``client_secret`` and a refresh rejected on the token are both "the
    refresh failed", and reporting one as the other sends an admin to generate a new token when
    the one they have is fine (SnelStart's two-credentials rule, CLAUDE.md §10).
    """

    code = "meta_app_secret"
    status_code = 409


class MetaPermissionError(MetaError):
    """Codes 10 and 200–299 — the token is alive and may not do this.

    Usually one of two things: the scope was not ticked when the token was generated, or the
    asset was never assigned to the system user in Business Settings.
    """

    code = "meta_permission"
    status_code = 409


class MetaRateLimited(MetaError):
    """Codes 4, 17, 32, 613, 80000–80014 — a rate, not a verdict."""

    code = "meta_rate_limited"
    status_code = 429


class MetaInvalid(MetaError):
    """Code 100 and the Marketing API's validation families — Meta refused what was sent."""

    code = "meta_invalid"
    status_code = 422


class MetaNotFound(MetaError):
    """The object does not exist, or this token cannot see it. Meta does not say which."""

    code = "meta_not_found"
    status_code = 404


class MetaDuplicate(MetaError):
    """Code 506 — an identical post was just made. The one refusal that is good news."""

    code = "meta_duplicate"
    status_code = 409


class MetaPolicyBlock(MetaError):
    """Code 368 — Meta blocked the action for policy reasons. A person reads this, not a loop."""

    code = "meta_policy_block"
    status_code = 409


class MetaVersionError(MetaError):
    """Code 2635 — the pinned API version is no longer served. Nothing a tenant can fix."""

    code = "meta_version"
    status_code = 502


class MetaUnavailable(MetaError):
    """A timeout, a connection failure, a 5xx, or Meta's own codes 1 and 2. Retryable."""

    code = "meta_unreachable"
    status_code = 502


_RATE_CODES = frozenset({4, 17, 32, 341, 613})
_UNAVAILABLE_CODES = frozenset({1, 2})
#: Subcodes of code 100 that mean "no such object" rather than "bad parameter".
_NOT_FOUND_SUBCODES = frozenset({33})
#: App-credential refusals. 101 is "invalid application id / secret"; 1 with the OAuth type is
#: what the token endpoint answers a wrong ``client_secret`` with.
_APP_CODES = frozenset({101})


def _blame(error: dict[str, Any]) -> tuple[str, ...]:
    """The field names a Marketing API validation refusal blamed, flattened."""
    data = error.get("error_data")
    if isinstance(data, str):
        # Meta sometimes sends ``error_data`` as a JSON string rather than an object.
        try:
            import json

            data = json.loads(data)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return ()
    out: list[str] = []
    for spec in data.get("blame_field_specs") or ():
        if isinstance(spec, list | tuple):
            name = ".".join(str(part) for part in spec if part not in (None, ""))
        else:
            name = str(spec)
        if name and name not in out:
            out.append(name)
    single = data.get("blame_field")
    if single and str(single) not in out:
        out.append(str(single))
    return tuple(out)


def classify(
    payload: dict[str, Any] | None,
    *,
    status: int,
    fallback: str = "",
    secrets: tuple[str | None, ...] = (),
    retry_after_minutes: int | None = None,
) -> MetaError:
    """Meta's refusal, as the exception that names what to do about it."""
    error = (payload or {}).get("error")
    if not isinstance(error, dict):
        error = {}
    code = _int(error.get("code"))
    subcode = _int(error.get("error_subcode"))
    trace = str(error.get("fbtrace_id") or "") or None
    # The user-facing pair is the more useful sentence where Meta sends it: ``message`` for an
    # ads refusal is often just "Invalid parameter" while ``error_user_msg`` says which budget.
    parts = [
        str(error.get("error_user_title") or "").strip(),
        str(error.get("error_user_msg") or "").strip(),
    ]
    prose = " — ".join(p for p in parts if p) or str(error.get("message") or "").strip()
    message = scrub(prose or fallback or f"HTTP {status}", *secrets)[:500]
    kwargs: dict[str, Any] = {
        "status": status,
        "meta_code": code,
        "subcode": subcode,
        "trace_id": trace,
    }

    if code == 190:
        return MetaAuthError(message, **kwargs)
    if code in _APP_CODES:
        return MetaAppSecretError(message, **kwargs)
    if code in _RATE_CODES or (code is not None and 80000 <= code <= 80014):
        return MetaRateLimited(message, retry_after_minutes=retry_after_minutes, **kwargs)
    if code == 10 or (code is not None and 200 <= code <= 299):
        return MetaPermissionError(message, **kwargs)
    if code == 368:
        return MetaPolicyBlock(message, **kwargs)
    if code == 506:
        return MetaDuplicate(message, **kwargs)
    if code == 2635:
        return MetaVersionError(message, **kwargs)
    if code == 803 or (code == 100 and subcode in _NOT_FOUND_SUBCODES):
        return MetaNotFound(message, **kwargs)
    if code in _UNAVAILABLE_CODES or status >= 500:
        return MetaUnavailable(message, **kwargs)
    if code is not None:
        # Anything Meta classified and we hold no mapping for is still a refusal of what was
        # sent — 100, the 1487xxx/1815xxx/1885xxx ads families, 3858xxx (the DSA fields).
        return MetaInvalid(message, blame_fields=_blame(error), **kwargs)
    if status == 401:
        return MetaAuthError(message, **kwargs)
    if status == 403:
        return MetaPermissionError(message, **kwargs)
    if status == 404:
        return MetaNotFound(message, **kwargs)
    if status == 429:
        return MetaRateLimited(message, retry_after_minutes=retry_after_minutes, **kwargs)
    if 400 <= status < 500:
        return MetaInvalid(message, **kwargs)
    return MetaError(message, **kwargs)


def is_retryable(exc: MetaError) -> bool:
    """Whether waiting could plausibly change the answer — for a **read** only."""
    return isinstance(exc, MetaUnavailable | MetaRateLimited)


def describe_failure(exc: Exception) -> str:
    """One line for a row's ``last_error``: what Meta said, scrubbed and capped."""
    if isinstance(exc, MetaError):
        codes = ""
        if exc.meta_code is not None:
            codes = f" ({exc.meta_code}" + (f"/{exc.subcode}" if exc.subcode else "") + ")"
        return f"{exc}{codes}"[:500]
    return scrub(str(exc))[:500]


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None

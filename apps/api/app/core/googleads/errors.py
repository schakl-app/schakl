"""Google Ads failures, classified — and scrubbed before anything leaves this process.

Google Ads does not answer like the rest of Google. A refusal arrives as a ``google.rpc.Status``
whose ``details[]`` carries a **GoogleAdsFailure**, and the thing that actually says what went
wrong is a *oneof* buried three levels down::

    {"error": {
      "code": 403, "message": "…", "status": "PERMISSION_DENIED",
      "details": [{
        "@type": "type.googleapis.com/google.ads.googleads.v25.errors.GoogleAdsFailure",
        "errors": [{
          "errorCode": {"authorizationError": "USER_PERMISSION_DENIED"},
          "message": "User doesn't have permission to access customer.",
          "location": {...}, "trigger": {...}}],
        "requestId": "…"}]}}

``errorCode`` has **167** possible member names in v25 and exactly one is ever set. Reading only
the HTTP status throws the diagnosis away: 403 is `USER_PERMISSION_DENIED` (this login has no
grant on that account), `DEVELOPER_TOKEN_NOT_APPROVED` (the agency never finished the API Center
application) and `CUSTOMER_NOT_ENABLED` (the client's account is suspended) — three different
sentences, three different people who can fix it, one status code.

Two rules do not bend:

* **The envelope carries an i18n key, never Google's text** (CLAUDE.md §9). Google's own sentence
  is genuinely useful, so it goes on the account row's ``last_error`` where an admin can read it
  and a translator never has to.
* **Nothing that leaves here contains a credential.** :func:`scrub` runs over every outgoing
  string. A developer token is an opaque 22-character secret that Google happily echoes back
  inside ``trigger`` when *it* is the thing at fault, and an OAuth bearer can reach a log line
  through ``httpx``'s own request repr. Both are redacted by pattern, so a token this module has
  never been handed is caught too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.errors import AppError

#: The prefix Google gives the failure payload inside ``details[]``. Matched by suffix so a
#: version bump (``…v25.errors.GoogleAdsFailure`` → ``…v26.…``) needs no edit here.
_FAILURE_TYPE_SUFFIX = "GoogleAdsFailure"

#: Credential shapes redacted from every outgoing string. OAuth refresh tokens (``1//…``),
#: access tokens (``ya29.…``), Google OAuth client secrets (``GOCSPX-…``) and client ids. The
#: developer token has no distinguishing shape, so it is scrubbed by *value* — see
#: :func:`scrub`'s ``extra`` argument, which the client passes the token it just used.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"1//[A-Za-z0-9_\-]{10,}"),
    re.compile(r"ya29\.[A-Za-z0-9_\-.]{10,}"),
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"[0-9]+-[a-z0-9]{16,}\.apps\.googleusercontent\.com"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.]{10,}"),
)

REDACTED = "[redacted]"


def scrub(text: str, *extra: str | None) -> str:
    """``text`` with every known credential shape — and every value in ``extra`` — redacted.

    ``extra`` is how a secret with no recognisable shape gets caught: the caller passes the
    developer token it was about to use, and a Google error that quotes it back comes out clean.
    Short values are ignored, because redacting a two-character "token" would eat the message.
    """
    out = text
    for value in extra:
        if value and len(value) >= 8:
            out = out.replace(value, REDACTED)
    for pattern in _PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


class AdsError(AppError):
    """A Google Ads call failed. ``str(exc)`` is Google's own text, already scrubbed.

    **It is an** :class:`~app.errors.AppError`, so any route that reaches Google surfaces the
    right status and i18n key without remembering to catch anything. The alternative — a plain
    exception plus a ``try``/``except`` per route — needs every future endpoint to opt in, and
    the one that forgets answers 500 with Google's own sentence in the log and nothing on screen.

    The two-audience split is kept by construction: ``message_key`` is what the envelope carries
    (§9), while ``str(exc)`` stays Google's text for the account row's ``last_error``, where an
    admin can act on it. ``AppError.__init__`` would overwrite the latter with the former, which
    is why the base initialiser is set up field by field here instead of called.
    """

    #: The envelope code, and the i18n key as ``errors.<code>``. Subclasses override both.
    code = "google_ads_error"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        Exception.__init__(self, message)
        self.code = type(self).code
        self.message_key = f"errors.{type(self).code}"
        self.status_code = type(self).status_code
        self.fields: dict[str, str] | None = None
        #: Google's own **identifiers**, carried onto the envelope (``AppError.details``) so a
        #: refusal is diagnosable at all: before this, a write failure arrived as a bare
        #: ``google_ads_error`` and the field path Google named was discarded at the boundary by
        #: the one class built to preserve it — ``str(exc)`` only ever reaches an admin through
        #: ``last_error``, which ``verify`` and the sync write and no write path does.
        #:
        #: Deliberately **not** ``str(exc)``: Google's prose stays out of the envelope
        #: (``test_google_s_own_text_never_reaches_the_envelope``) because the envelope's message
        #: is an i18n key (§9) and untranslated provider text in it is a screen in the wrong
        #: language. An error code and a request id are identifiers, not prose — the first is
        #: what §5 calls the only reliable way to tell two refusals apart, and the second is the
        #: one thing Google support asks for.
        self.details: dict[str, Any] | None = {
            key: value
            for key, value in (
                ("google_error_code", error_code),
                ("google_request_id", request_id),
            )
            if value
        } or None
        self.status = status
        #: The GAQL/API error enum, as ``"<group>.<VALUE>"`` (``"quotaError.RESOURCE_EXHAUSTED"``).
        #: The only reliable way to tell two refusals apart that share a status code.
        self.error_code = error_code
        #: Google's own request id, from the failure payload. The one thing Google support asks
        #: for, so it is worth keeping even though it never reaches a client.
        self.request_id = request_id

    def as_app_error(self) -> AppError:
        """Kept for call sites that want a plain envelope error rather than this subclass."""
        return AppError(
            self.code, self.message_key, status_code=self.status_code, details=self.details
        )


class AdsNotConfigured(AdsError):
    """No developer token, no linked account, or the module contributing them is not installed.

    **A presentable state, not a bug.** Every consumer turns this into a labelled "Ads is not set
    up yet" rather than an error: the picker says so, the dashboard tile says so, and the sync
    records it and moves on. Raised — never returned as ``None`` — because a ``None`` customer id
    reaches the URL builder and asks Google about a customer called "None", which comes back 404
    and reads as a sunset API version.
    """

    code = "google_ads_not_configured"
    status_code = 409


class AdsAuthError(AdsError):
    """The OAuth grant or the developer token is the problem. Retrying cannot help."""

    code = "google_ads_auth"
    status_code = 409


class AdsPermissionError(AdsError):
    """The credential is fine; this login may not touch this customer."""

    code = "google_ads_permission"
    status_code = 409


class AdsDeveloperTokenError(AdsError):
    """The developer token is invalid, unapproved, or not on the allow-list for this call.

    Split from :class:`AdsAuthError` because the fix is a different person: re-connecting Google
    does nothing, and someone has to finish the application in the Google Ads API Center.
    """

    code = "google_ads_developer_token"
    status_code = 409


class AdsQuotaError(AdsError):
    """Daily operations, or short/long-term query resource consumption, exhausted."""

    code = "google_ads_quota"
    status_code = 429

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Seconds Google asked us to wait, from ``QuotaErrorDetails.retryDelay``. Google is the
        #: authority on its own backoff; our jittered ladder is only the fallback.
        self.retry_after = retry_after


class AdsQueryError(AdsError):
    """The GAQL was rejected — an unknown field, a bad date, an unselectable combination."""

    code = "google_ads_query"
    status_code = 422


class AdsRequestError(AdsError):
    """The request we built was refused: a missing required field, a bad enum, a bad mask.

    Split from :class:`AdsQueryError` because a mutate contains no GAQL, and telling somebody
    the query was rejected when they were creating a campaign is the most misleading sentence
    available — ``requestError`` used to land here and answered *"the GAQL was rejected"* for a
    campaign removal. Split from the base :class:`AdsError` because that answers **502**, and a
    payload Google refused is a fault on this side: 502 reads as "Google is down" and sends
    somebody to check a status page over a missing field.
    """

    code = "google_ads_request"
    status_code = 422


class AdsVersionError(AdsError):
    """A 404 on a versioned path: this Ads API version is sunset.

    Its own class because it is the failure that looks like every other failure. Google answers
    **404** on every path under a retired version, which is not a credential problem, an account
    problem or a scope problem — so a screen that classifies it as any of those tells the admin
    to go and fix something that was never broken. ``SCHAKL_GOOGLE_ADS_API_VERSION`` is the
    escape hatch on a box that outlives its release.
    """

    code = "google_ads_api_version"
    status_code = 502


class AdsUnavailable(AdsError):
    """Google could not be reached, or answered 5xx after the retries were spent."""

    code = "google_ads_unreachable"
    status_code = 502


#: ``errorCode`` oneof member → the class it maps to, before the per-value refinements below.
_BY_GROUP: dict[str, type[AdsError]] = {
    "authenticationError": AdsAuthError,
    "authorizationError": AdsPermissionError,
    "quotaError": AdsQuotaError,
    "queryError": AdsQueryError,
    "requestError": AdsRequestError,
    "fieldError": AdsRequestError,
    "fieldMaskError": AdsRequestError,
    "mutateError": AdsRequestError,
    "headerError": AdsAuthError,
    "internalError": AdsUnavailable,
}

#: The values that outrank their group. Every one of these arrives inside an
#: authentication/authorization error and means "the *developer token* is the problem", which
#: sends an admin somewhere completely different from "reconnect your Google account".
_DEVELOPER_TOKEN_VALUES = frozenset(
    {
        "DEVELOPER_TOKEN_INVALID",
        "DEVELOPER_TOKEN_NOT_APPROVED",
        "DEVELOPER_TOKEN_PROHIBITED",
        "DEVELOPER_TOKEN_NOT_ON_ALLOWLIST",
        "ORGANIZATION_NOT_ASSOCIATED_WITH_DEVELOPER_TOKEN",
    }
)


def _failure(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The GoogleAdsFailure out of a ``google.rpc.Status``'s ``details[]``, if there is one."""
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    for detail in error.get("details") or ():
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith(_FAILURE_TYPE_SUFFIX):
            return detail
    return None


def _first_error(failure: dict[str, Any]) -> dict[str, Any] | None:
    for item in failure.get("errors") or ():
        if isinstance(item, dict):
            return item
    return None


def _retry_delay(item: dict[str, Any]) -> float | None:
    """``QuotaErrorDetails.retryDelay`` in seconds. Google sends a protobuf Duration string."""
    details = item.get("details")
    if not isinstance(details, dict):
        return None
    quota = details.get("quotaErrorDetails")
    if not isinstance(quota, dict):
        return None
    raw = str(quota.get("retryDelay") or "").strip()
    if not raw.endswith("s"):
        return None
    try:
        return float(raw[:-1])
    except ValueError:
        return None


def _typed(
    item: dict[str, Any],
    *,
    status: int | None,
    fallback: str,
    secret: str | None,
    request_id: str | None,
) -> AdsError:
    """One ``GoogleAdsError`` → the class it means, scrubbed.

    Factored out of :func:`classify` because a partial-failure batch needs the same mapping
    applied to *every* error rather than the first — and the second copy of a 167-way oneof
    reader is the one that would drift.
    """
    codes = item.get("errorCode")
    group, value = "", ""
    if isinstance(codes, dict):
        for key, raw in codes.items():
            if raw:
                group, value = str(key), str(raw)
                break
    message = str(item.get("message") or fallback or value or "Google Ads error")
    # A *named* group is Google telling us which of the 167 things went wrong, which makes it a
    # refusal of what we sent — not an outage. Only a failure we could not read at all falls back
    # to `AdsError` and its 502; anything Google classified and we have no mapping for is still a
    # caller error, so a group added to the API next year lands on the right side of that line.
    cls = _BY_GROUP.get(group) or (AdsRequestError if group else AdsError)
    if value in _DEVELOPER_TOKEN_VALUES:
        cls = AdsDeveloperTokenError
    kwargs: dict[str, Any] = {}
    if cls is AdsQuotaError:
        kwargs["retry_after"] = _retry_delay(item)
    return cls(
        scrub(message, secret),
        status=status,
        error_code=f"{group}.{value}" if group else value or None,
        request_id=request_id,
        **kwargs,
    )


def _operation_index(item: dict[str, Any]) -> int | None:
    """Which operation in the request this error is about.

    Google documents it as *"typically ``location.field_path_elements[0].index``"* — typically,
    because the path walks into whatever field failed and the operations list is only usually
    first. So the element **named** ``operations`` is preferred and position is the fallback:
    reading index 0 blindly attributes a nested failure to the wrong row, and a wrong row in a
    write report is worse than no row at all.
    """
    location = item.get("location")
    if not isinstance(location, dict):
        return None
    elements = [e for e in location.get("fieldPathElements") or () if isinstance(e, dict)]
    for element in elements:
        if str(element.get("fieldName") or "") == "operations" and element.get("index") is not None:
            return int(element["index"])
    for element in elements:
        if element.get("index") is not None:
            return int(element["index"])
    return None


@dataclass(frozen=True)
class OperationFailure:
    """One refused operation out of a ``partialFailure`` batch."""

    #: ``None`` when Google's error carried no path back to an operation. Reported as an
    #: unattributed failure rather than pinned on operation 0.
    index: int | None
    error: AdsError


def partial_failures(
    payload: dict[str, Any] | None, *, secret: str | None = None
) -> list[OperationFailure]:
    """The per-operation refusals inside a **successful** ``:mutate`` response.

    This is the shape :func:`classify` cannot see, and the reason it needs its own function.
    With ``partialFailure: true`` Google answers **HTTP 200** — the valid operations were applied
    — and puts the refusals in a top-level ``partialFailureError``, which is a bare
    ``google.rpc.Status`` rather than the ``{"error": …}`` envelope every failure path here walks.
    A caller that only classifies non-2xx responses therefore reads "eleven of twelve exclusions
    were written" as "twelve were written", which is a report that is wrong in the direction
    nobody checks.

    ``results`` still carries one entry per operation and the refused ones are **empty objects**,
    so the index is the only link between a result slot and its reason.
    """
    status = (payload or {}).get("partialFailureError")
    if not isinstance(status, dict):
        return []
    failure = _failure({"error": status})
    if failure is None:
        # A Status carrying no GoogleAdsFailure is still a refusal — surfaced unattributed
        # rather than dropped, because "something in this batch failed and we do not know what"
        # is a true sentence and silence is not.
        message = scrub(str(status.get("message") or ""), secret)
        return [OperationFailure(None, AdsError(message or "partial failure", status=200))]
    request_id = str(failure.get("requestId") or "") or None
    out: list[OperationFailure] = []
    for item in failure.get("errors") or ():
        if not isinstance(item, dict):
            continue
        out.append(
            OperationFailure(
                _operation_index(item),
                _typed(item, status=200, fallback="", secret=secret, request_id=request_id),
            )
        )
    return out


def classify(
    payload: dict[str, Any] | None,
    *,
    status: int | None = None,
    fallback: str = "",
    secret: str | None = None,
) -> AdsError:
    """Turn a Google Ads error body into the typed, scrubbed failure it describes.

    ``secret`` is the developer token used for the call — scrubbed out by value, because it has
    no pattern to recognise it by and Google quotes it back when it is the thing at fault.

    Falls back on the HTTP status when the body is unparseable, which is not hypothetical: a
    gateway between here and Google answers HTML, and a sunset version answers a 404 whose body
    says nothing about Ads at all.
    """
    failure = _failure(payload or {})
    item = _first_error(failure) if failure else None
    request_id = str(failure.get("requestId") or "") or None if failure else None

    if item is not None:
        return _typed(
            item, status=status, fallback=fallback, secret=secret, request_id=request_id
        )

    # No parseable failure. The status is all we have — and one of them is load-bearing.
    message = ""
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        message = str(payload["error"].get("message") or "")
    message = scrub(message or fallback or f"HTTP {status}", secret)
    if status == 404:
        return AdsVersionError(message, status=status)
    if status in (401, 403):
        return AdsAuthError(message, status=status)
    if status == 429:
        return AdsQuotaError(message, status=status)
    if status is not None and status >= 500:
        return AdsUnavailable(message, status=status)
    return AdsError(message, status=status)


def describe_failure(exc: AdsError) -> str:
    """A refusal as one line for the account row's ``last_error``.

    The status and the error enum are the diagnosis and neither is in ``str(exc)``. Truncated at
    500 to match the column, and already scrubbed by :func:`classify`.
    """
    parts = [p for p in (f"HTTP {exc.status}" if exc.status else "", exc.error_code) if p]
    prefix = f"{' '.join(parts)}: " if parts else ""
    return f"{prefix}{exc}"[:500]

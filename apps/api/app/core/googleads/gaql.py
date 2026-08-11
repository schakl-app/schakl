"""The guard on the GAQL passthrough (`POST /google-ads/accounts/{id}/query`).

The proof-of-concept this module replaces refused to have a raw-query tool at all, and it was
right about the danger and wrong about the remedy. A named tool per question means the one
question nobody predicted needs a release; and the three risks it named are each answerable.

**Cross-account access is not one of them, and that is structural.** The customer id lives in the
URL path, which this module builds from its own row — a GAQL query cannot name a customer, so no
amount of cleverness in the text reaches another advertiser. Neither is mutation: GAQL has no
write syntax, and ``googleAds:search`` has no mutate verb. What is left is genuinely ours to
bound:

* **Which resources may be read.** v25 exposes 183, and a handful of them are nothing to do with
  advertising performance: ``customer_user_access`` lists the e-mail address of every human with
  a login, ``billing_setup`` and ``account_budget`` carry payment arrangements, ``change_event``
  names who changed what. An allow-list is the only honest way to say "reporting, not the account
  file" — and it is an allow-list, never a deny-list, so a resource Google adds next quarter is
  refused until someone looks at it.
* **How much it may cost.** Every metrics query is charged against the agency's shared daily
  operation quota, and v25 added ``EXCESSIVE_SHORT_TERM_QUERY_RESOURCE_CONSUMPTION`` precisely
  because one query can be expensive enough to matter. So a `LIMIT` is imposed rather than
  requested, and a metrics query without a date bound — the single most expensive shape available
  — is refused with a message that says how to fix it.

Parsing is **quote-aware**, not a regex. ``WHERE campaign.name LIKE '%FROM US%'`` contains the
word FROM, and a guard that finds the resource with ``\\bFROM\\s+(\\w+)`` reads the allow-list
check against a string literal an author controls. Everything here scans the text tracking quote
state and only recognises a keyword outside one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.errors import AppError

#: The resources the passthrough may read. Deliberately reporting-shaped: campaigns and their
#: structure, what was searched, what it cost, what changed and what Google suggests. Ordered as
#: the docs group them so an addition is easy to place.
#:
#: Absent on purpose (each is a decision, not an oversight):
#:   ``customer_user_access``/``customer_user_access_invitation`` — every login's e-mail address
#:   ``billing_setup``/``account_budget``/``account_budget_proposal``/``invoice`` — payment data
#:   ``customer_client_link``/``customer_manager_link`` — the MCC's whole client tree, which is
#:       how a scoped key would enumerate accounts it was never linked to
#:   ``feed*``/``batch_job``/``offline_user_data_job`` — write plumbing with no read value here
ALLOWED_RESOURCES: frozenset[str] = frozenset(
    {
        # Account
        "customer",
        "customer_client",
        # Structure
        "campaign",
        "campaign_budget",
        "campaign_criterion",
        "campaign_shared_set",
        "shared_set",
        "shared_criterion",
        "ad_group",
        "ad_group_criterion",
        "ad_group_ad",
        "asset",
        "asset_group",
        "asset_group_signal",
        "audience",
        "user_list",
        # Performance
        "keyword_view",
        "search_term_view",
        "campaign_search_term_insight",
        "ad_group_ad_asset_view",
        "geographic_view",
        "user_location_view",
        "landing_page_view",
        "campaign_audience_view",
        "ad_group_audience_view",
        "detail_placement_view",
        # Conversions + goals
        "conversion_action",
        "customer_conversion_goal",
        "campaign_conversion_goal",
        "conversion_goal_campaign_config",
        # Change history + advice
        "change_event",
        "change_status",
        "recommendation",
        # Lookups
        "geo_target_constant",
        "language_constant",
    }
)

#: Hard ceiling on a passthrough read, whatever the caller asks for. Google's own page size is a
#: fixed 10 000 and the client would happily walk every page; this is the product limit that says
#: an answer nobody can read is not an answer. Over it is **clamped and reported**, never
#: silently truncated — the response carries a warning naming the number that was applied.
MAX_LIMIT = 2_000

#: Applied when the caller wrote no `LIMIT` at all. Small on purpose: a passthrough is for
#: answering a question, and a question that needs two thousand rows wants an endpoint.
DEFAULT_LIMIT = 200

_KEYWORDS = ("select", "from", "where", "order by", "limit", "parameters")

_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: A date bound this recognises as bounding. ``DURING`` takes a named range, ``BETWEEN`` two
#: literals, and the comparison operators are how a half-open window is usually written; any of
#: them ends the "scan all of history" shape. ``segments.week``/``month``/``quarter``/``year``
#: are date fields too, and filtering on one bounds the read just as well.
_DATE_FIELD_RE = re.compile(
    r"\bsegments\.(date|week|month|quarter|year)\b|\bchange_event\.change_date_time\b"
    r"|\bchange_status\.last_change_date_time\b",
    re.IGNORECASE,
)

_METRICS_RE = re.compile(r"\bmetrics\.[a-z_]+", re.IGNORECASE)


@dataclass(frozen=True)
class CheckedQuery:
    """A GAQL query that passed the guard, plus what the guard changed about it."""

    query: str
    resource: str
    limit: int
    #: i18n keys for anything the caller should know was applied to their query. Reported in the
    #: response's ``warnings`` — a clamp nobody is told about is a truncation (CLAUDE.md §17).
    warnings: tuple[str, ...] = ()


def _mask_literals(query: str) -> str:
    """``query`` with every quoted literal replaced by spaces of the same length.

    Keeps offsets identical to the original so a keyword found here can be sliced out of the
    real text, while making it impossible for a string literal to be read as syntax. GAQL quotes
    with ``'`` or ``"`` and has no escape sequence inside them.
    """
    out = list(query)
    quote: str | None = None
    for i, ch in enumerate(query):
        if quote is None:
            if ch in "'\"":
                quote = ch
                out[i] = " "
        else:
            out[i] = " "
            if ch == quote:
                quote = None
    if quote is not None:
        raise AppError(
            "google_ads_query_invalid",
            "errors.google_ads_query_unterminated_string",
            status_code=422,
        )
    return "".join(out)


def _keyword_pattern(keyword: str) -> str:
    """``"order by"`` → a pattern tolerating any whitespace between the words.

    A formatted query wraps before BY as readily as after it, and a guard that only recognises
    exactly one space silently stops seeing the clause — which for ORDER BY would mean the
    LIMIT this module appends lands *before* it and Google rejects the whole query.
    """
    words = r"\s+".join(re.escape(word) for word in keyword.split())
    return rf"(?<![a-z0-9_.]){words}(?![a-z0-9_])"


def _clauses(query: str) -> dict[str, tuple[int, int]]:
    """Top-level clause keyword → ``(start, end)`` offsets of its *body* in the original text."""
    masked = _mask_literals(query).lower()
    found: list[tuple[int, int, str]] = []
    for keyword in _KEYWORDS:
        for match in re.finditer(_keyword_pattern(keyword), masked):
            found.append((match.start(), match.end(), keyword))
    found.sort()
    spans: dict[str, tuple[int, int]] = {}
    starts: dict[str, int] = {}
    for index, (start, end, keyword) in enumerate(found):
        if keyword in spans:
            # A second SELECT or FROM is not GAQL; refusing beats guessing which one is meant.
            raise AppError(
                "google_ads_query_invalid",
                "errors.google_ads_query_repeated_clause",
                status_code=422,
            )
        body_end = found[index + 1][0] if index + 1 < len(found) else len(query)
        spans[keyword] = (end, body_end)
        starts[keyword] = start
    # The keyword's own start, kept beside the body so a caller can cut the clause out whole
    # without re-deriving it from ``len(keyword)`` — which is wrong the moment ORDER BY wraps.
    spans.update({f"@{name}": (offset, offset) for name, offset in starts.items()})
    return spans


def check(query: str, *, limit: int | None = None) -> CheckedQuery:
    """Validate and normalise one GAQL query, or raise the 422 that says which rule it broke.

    ``limit`` is the caller's requested cap; the smaller of it, any `LIMIT` in the text, and
    :data:`MAX_LIMIT` wins.
    """
    text = (query or "").strip().rstrip(";").strip()
    if not text:
        raise AppError("google_ads_query_invalid", "errors.google_ads_query_empty", status_code=422)

    masked = _mask_literals(text)
    if ";" in masked:
        # One statement per call. GAQL has no statement separator, so a semicolon outside a
        # literal is either a mistake or an attempt at something this endpoint does not do.
        raise AppError(
            "google_ads_query_invalid",
            "errors.google_ads_query_multiple_statements",
            status_code=422,
        )

    spans = _clauses(text)
    if "select" not in spans or "from" not in spans:
        raise AppError("google_ads_query_invalid", "errors.google_ads_query_shape", status_code=422)

    start, end = spans["from"]
    resource = text[start:end].strip().lower()
    if not _RESOURCE_RE.match(resource):
        raise AppError("google_ads_query_invalid", "errors.google_ads_query_shape", status_code=422)
    if resource not in ALLOWED_RESOURCES:
        # Names the parameter, not a verdict on the endpoint: the query is fine, this one
        # resource is not offered here.
        raise AppError(
            "google_ads_query_resource",
            "errors.google_ads_query_resource_not_allowed",
            status_code=422,
            fields={"query": "errors.google_ads_query_resource_not_allowed"},
        )

    select_start, select_end = spans["select"]
    fields = text[select_start:select_end]
    where = text[spans["where"][0] : spans["where"][1]] if "where" in spans else ""

    warnings: list[str] = []
    if _METRICS_RE.search(_mask_literals(fields)) and not _DATE_FIELD_RE.search(
        _mask_literals(where)
    ):
        # The single most expensive shape available, and the one whose answer is least likely to
        # be what was wanted: metrics with no date bound is every day the account has existed,
        # summed into one row per entity.
        raise AppError(
            "google_ads_query_unbounded",
            "errors.google_ads_query_needs_date_bound",
            status_code=422,
            fields={"query": "errors.google_ads_query_needs_date_bound"},
        )

    stated: int | None = None
    if "limit" in spans:
        raw = text[spans["limit"][0] : spans["limit"][1]].strip()
        if not raw.isdigit():
            raise AppError(
                "google_ads_query_invalid",
                "errors.google_ads_query_limit",
                status_code=422,
            )
        stated = int(raw)

    requested = min(v for v in (limit, stated, MAX_LIMIT) if v is not None and v > 0)
    effective = max(1, min(requested, MAX_LIMIT))
    if stated is not None and effective < stated:
        warnings.append("google_ads.warning.limit_clamped")
    if stated is None:
        effective = min(effective, limit or DEFAULT_LIMIT, MAX_LIMIT)

    # Rebuild rather than edit in place: the LIMIT is ours to impose, and appending to a query
    # that already ends in one would be a syntax error. GAQL fixes the clause order, so the
    # pieces are cut at their own keyword offsets and reassembled — LIMIT then PARAMETERS.
    params = ""
    if "parameters" in spans:
        params = text[spans["@parameters"][0] :].strip()
    cut = (
        min(
            offset
            for name, offset in (
                (n, spans[f"@{n}"][0]) for n in ("limit", "parameters") if n in spans
            )
        )
        if ("limit" in spans or "parameters" in spans)
        else len(text)
    )
    body = text[:cut].rstrip()
    rebuilt = f"{body} LIMIT {effective}" + (f" {params}" if params else "")

    return CheckedQuery(
        query=" ".join(rebuilt.split()),
        resource=resource,
        limit=effective,
        warnings=tuple(warnings),
    )

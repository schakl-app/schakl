"""The Google Ads seam's two pieces that must be right before anything is built on them:
the GAQL guard and the failure classifier.

Neither touches the database or the network, so this file is fast and can be run alone.
"""

from __future__ import annotations

import pytest

from app.core.googleads import client as ads_client_mod
from app.core.googleads import gaql
from app.core.googleads.errors import (
    REDACTED,
    AdsAuthError,
    AdsDeveloperTokenError,
    AdsPermissionError,
    AdsQueryError,
    AdsQuotaError,
    AdsUnavailable,
    AdsVersionError,
    classify,
    describe_failure,
    scrub,
)
from app.errors import AppError

# --- the GAQL guard ---------------------------------------------------------------------- #


def _failure(group: str, value: str, *, message: str = "nope", details: dict | None = None) -> dict:
    error: dict = {
        "errorCode": {group: value},
        "message": message,
    }
    if details:
        error["details"] = details
    return {
        "error": {
            "code": 400,
            "message": message,
            "details": [
                {
                    "@type": (
                        "type.googleapis.com/google.ads.googleads.v25.errors.GoogleAdsFailure"
                    ),
                    "errors": [error],
                    "requestId": "req-1",
                }
            ],
        }
    }


def test_a_plain_query_gets_the_default_limit() -> None:
    checked = gaql.check("SELECT campaign.id, campaign.name FROM campaign")
    assert checked.resource == "campaign"
    assert checked.limit == gaql.DEFAULT_LIMIT
    assert checked.query.endswith(f"LIMIT {gaql.DEFAULT_LIMIT}")
    assert checked.warnings == ()


def test_a_stated_limit_is_honoured_and_never_doubled() -> None:
    checked = gaql.check("SELECT campaign.id FROM campaign LIMIT 5")
    assert checked.limit == 5
    # The rebuild must not leave the caller's own LIMIT behind it.
    assert checked.query.lower().count("limit") == 1
    assert checked.query == "SELECT campaign.id FROM campaign LIMIT 5"


def test_an_oversized_limit_is_clamped_and_says_so() -> None:
    """Clamped, never silently truncated — the caller is told the number that was applied."""
    checked = gaql.check("SELECT campaign.id FROM campaign LIMIT 999999")
    assert checked.limit == gaql.MAX_LIMIT
    assert "google_ads.warning.limit_clamped" in checked.warnings


def test_order_by_survives_the_rebuilt_limit() -> None:
    checked = gaql.check(
        "SELECT campaign.name, metrics.cost_micros FROM campaign "
        "WHERE segments.date DURING LAST_30_DAYS ORDER BY metrics.cost_micros DESC LIMIT 10"
    )
    assert checked.query.index("ORDER BY") < checked.query.index("LIMIT")


def test_order_by_wrapped_across_lines_is_still_recognised() -> None:
    """A formatted query wraps between ORDER and BY; a guard that misses it would append the
    LIMIT ahead of the clause and Google would reject the whole query."""
    checked = gaql.check(
        "SELECT campaign.name FROM campaign ORDER\n  BY campaign.name",
    )
    assert checked.query.index("ORDER") < checked.query.index("LIMIT")


def test_parameters_stays_last() -> None:
    checked = gaql.check("SELECT campaign.id FROM campaign LIMIT 5 PARAMETERS include_drafts=true")
    assert checked.query.endswith("PARAMETERS include_drafts=true")
    assert checked.query.index("LIMIT") < checked.query.index("PARAMETERS")


def test_a_resource_outside_the_allow_list_is_refused() -> None:
    """An allow-list, not a deny-list: the account's user list is not a reporting resource."""
    with pytest.raises(AppError) as exc:
        gaql.check("SELECT customer_user_access.email_address FROM customer_user_access")
    assert exc.value.message_key == "errors.google_ads_query_resource_not_allowed"
    assert exc.value.status_code == 422


def test_the_from_keyword_cannot_be_smuggled_in_a_string_literal() -> None:
    """The whole reason the parser masks literals: a campaign named "FROM billing_setup" must
    not move the allow-list check off the real resource."""
    checked = gaql.check(
        "SELECT campaign.id FROM campaign WHERE campaign.name = 'FROM billing_setup'"
    )
    assert checked.resource == "campaign"


def test_a_second_statement_is_refused() -> None:
    with pytest.raises(AppError) as exc:
        gaql.check("SELECT campaign.id FROM campaign; SELECT customer.id FROM customer")
    assert exc.value.message_key == "errors.google_ads_query_multiple_statements"


def test_a_repeated_clause_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(AppError) as exc:
        gaql.check("SELECT campaign.id FROM campaign FROM customer")
    assert exc.value.message_key == "errors.google_ads_query_repeated_clause"


def test_an_unterminated_string_is_refused() -> None:
    with pytest.raises(AppError) as exc:
        gaql.check("SELECT campaign.id FROM campaign WHERE campaign.name = 'oops")
    assert exc.value.message_key == "errors.google_ads_query_unterminated_string"


def test_metrics_without_a_date_bound_are_refused() -> None:
    """The most expensive shape available: every day the account has existed, in one row."""
    with pytest.raises(AppError) as exc:
        gaql.check("SELECT campaign.name, metrics.cost_micros FROM campaign")
    assert exc.value.message_key == "errors.google_ads_query_needs_date_bound"


@pytest.mark.parametrize(
    "where",
    [
        "WHERE segments.date DURING LAST_30_DAYS",
        "WHERE segments.date BETWEEN '2026-07-01' AND '2026-07-31'",
        "WHERE segments.date >= '2026-07-01' AND segments.date <= '2026-07-31'",
        "WHERE segments.month = '2026-07-01'",
    ],
)
def test_every_shape_of_date_bound_satisfies_the_rule(where: str) -> None:
    checked = gaql.check(f"SELECT campaign.name, metrics.clicks FROM campaign {where}")
    assert checked.limit == gaql.DEFAULT_LIMIT


def test_a_config_read_needs_no_date_bound() -> None:
    """Only *metrics* are charged by the day. Reading which keywords exist is not."""
    checked = gaql.check(
        "SELECT ad_group_criterion.keyword.text, ad_group_criterion.negative "
        "FROM ad_group_criterion"
    )
    assert checked.resource == "ad_group_criterion"


def test_an_empty_query_is_refused() -> None:
    with pytest.raises(AppError) as exc:
        gaql.check("   ")
    assert exc.value.message_key == "errors.google_ads_query_empty"


def test_a_non_numeric_limit_is_refused() -> None:
    with pytest.raises(AppError) as exc:
        gaql.check("SELECT campaign.id FROM campaign LIMIT ten")
    assert exc.value.message_key == "errors.google_ads_query_limit"


# --- the failure classifier -------------------------------------------------------------- #


def test_a_permission_refusal_is_not_an_authentication_one() -> None:
    """Both arrive as 403 and the fix is a different person for each."""
    exc = classify(_failure("authorizationError", "USER_PERMISSION_DENIED"), status=403)
    assert isinstance(exc, AdsPermissionError)
    assert exc.error_code == "authorizationError.USER_PERMISSION_DENIED"
    assert exc.request_id == "req-1"


def test_a_developer_token_problem_outranks_its_group() -> None:
    """DEVELOPER_TOKEN_NOT_APPROVED arrives inside an authorization error and means nobody
    should be told to reconnect their Google account."""
    exc = classify(_failure("authorizationError", "DEVELOPER_TOKEN_NOT_APPROVED"), status=403)
    assert isinstance(exc, AdsDeveloperTokenError)
    assert not isinstance(exc, AdsPermissionError)


def test_an_expired_oauth_token_is_an_auth_error() -> None:
    exc = classify(_failure("authenticationError", "OAUTH_TOKEN_EXPIRED"), status=401)
    assert isinstance(exc, AdsAuthError)


def test_a_quota_refusal_carries_googles_own_retry_delay() -> None:
    payload = _failure(
        "quotaError",
        "RESOURCE_TEMPORARILY_EXHAUSTED",
        details={"quotaErrorDetails": {"rateName": "Requests per account", "retryDelay": "30s"}},
    )
    exc = classify(payload, status=429)
    assert isinstance(exc, AdsQuotaError)
    assert exc.retry_after == 30.0


def test_a_bad_query_is_a_422_not_a_502() -> None:
    exc = classify(_failure("queryError", "UNRECOGNIZED_FIELD"), status=400)
    assert isinstance(exc, AdsQueryError)
    assert exc.as_app_error().status_code == 422


def test_a_404_is_read_as_a_sunset_api_version() -> None:
    """The failure that looks like every other failure: Google 404s every path under a retired
    version, which is neither a credential nor an account problem."""
    exc = classify(None, status=404, fallback="Not Found")
    assert isinstance(exc, AdsVersionError)
    assert exc.as_app_error().message_key == "errors.google_ads_api_version"


def test_an_unparseable_body_falls_back_on_the_status() -> None:
    """A gateway between here and Google answers HTML, not a GoogleAdsFailure."""
    exc = classify(None, status=503, fallback="<html>502 Bad Gateway</html>")
    assert isinstance(exc, AdsUnavailable)


def test_a_developer_token_never_reaches_the_message() -> None:
    """Google echoes the offending value back inside `trigger` when the token is at fault."""
    token = "AbCdEfGhIjKlMnOpQrStUv"
    payload = _failure(
        "authenticationError",
        "DEVELOPER_TOKEN_INVALID",
        message=f"Developer token {token} is not valid.",
    )
    exc = classify(payload, status=401, secret=token)
    assert token not in str(exc)
    assert REDACTED in str(exc)


def test_oauth_shapes_are_scrubbed_without_being_named() -> None:
    """A token this module was never handed is still caught, by shape."""
    text = "refresh 1//0gLongLookingRefreshToken and access ya29.a0AfB_byC-more"
    out = scrub(text)
    assert "1//0gLong" not in out
    assert "ya29." not in out


def test_describe_failure_leads_with_the_diagnosis() -> None:
    """Neither the status nor the enum is in ``str(exc)``, and they are the whole diagnosis."""
    exc = classify(_failure("authorizationError", "CUSTOMER_NOT_ENABLED"), status=403)
    line = describe_failure(exc)
    assert line.startswith("HTTP 403 authorizationError.CUSTOMER_NOT_ENABLED:")
    assert len(line) <= 500


# --- customer id normalisation ------------------------------------------------------------ #


@pytest.mark.parametrize(
    "raw",
    ["123-456-7890", "1234567890", "customers/1234567890", " 123 456 7890 "],
)
def test_every_way_an_account_id_arrives_normalises_to_the_same_digits(raw: str) -> None:
    assert ads_client_mod.normalise_customer_id(raw) == "1234567890"


def test_the_display_form_is_the_one_googles_own_ui_shows() -> None:
    assert ads_client_mod.format_customer_id("1234567890") == "123-456-7890"


def test_a_retry_is_never_offered_for_a_refusal_waiting_cannot_fix() -> None:
    """The daily allowance will not reset inside a request; a per-minute rate might."""
    daily = classify(_failure("quotaError", "RESOURCE_EXHAUSTED"), status=429)
    short = classify(_failure("quotaError", "RESOURCE_TEMPORARILY_EXHAUSTED"), status=429)
    denied = classify(_failure("authorizationError", "USER_PERMISSION_DENIED"), status=403)
    assert not ads_client_mod._is_retryable(daily)
    assert ads_client_mod._is_retryable(short)
    assert not ads_client_mod._is_retryable(denied)

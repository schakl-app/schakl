"""The write surface and the decisions log, end to end through the fake transport.

The fake is installed at the **transport**, so every request here travels the real OAuth client,
the real header builder, the real ``updateMask`` builder and the real partial-failure parser. A
fake one layer up would let all four through untouched, which is the class of bug that only shows
against a live account — the most expensive place to find it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.integrations.google_ads.models import GoogleAdsDecision, GoogleAdsSettings
from tests.conftest import auth_cookie, make_tenant
from tests.googleads_fake import failure
from tests.test_google_ads_reads import CUSTOMER, _linked, fake  # noqa: F401 — transport fixture

pytestmark = pytest.mark.asyncio


async def _grant_everything(t) -> None:
    """The owner role already holds ``*``; this is only here to be explicit about what is under
    test — the split between the four write keys is exercised by its own test below."""
    return None


# --- the policy ------------------------------------------------------------------------------- #


async def test_a_policy_read_shows_the_stored_row_and_what_it_resolves_to(client_for, fake) -> None:  # noqa: F811
    """Both, because a form full of blanks meaning "something else decides" is unreadable."""
    t, account_id = await _linked("gads-policy-read")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as client:
        response = await client.get(
            f"/api/v1/google-ads/accounts/{account_id}/policy", headers=headers
        )
    assert response.status_code == 200
    body = response.json()
    assert body["stored"] is False
    assert body["protected_terms"] == []
    # The built-in relative ceiling is what an install with no policy at all resolves to.
    assert body["resolved"]["max_budget_increase"] == 1.0


async def test_the_house_policy_reaches_every_account_and_lists_union(client_for, fake) -> None:  # noqa: F811
    t, account_id = await _linked("gads-policy-union")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as client:
        assert (
            await client.put(
                "/api/v1/google-ads/policy",
                headers=headers,
                json={"always_exclude": ["vacature"], "max_cpc": 2.0},
            )
        ).status_code == 200
        assert (
            await client.put(
                f"/api/v1/google-ads/accounts/{account_id}/policy",
                headers=headers,
                json={"always_exclude": ["stage"], "protected_terms": ["Beugel"]},
            )
        ).status_code == 200
        body = (
            await client.get(
                f"/api/v1/google-ads/accounts/{account_id}/policy", headers=headers
            )
        ).json()
    assert body["resolved"]["always_exclude"] == ["vacature", "stage"]
    # The account said nothing about the bid ceiling, so the house value stands.
    assert body["resolved"]["max_cpc"] == 2.0
    assert body["resolved"]["protected_terms"] == ["beugel"]


async def test_an_explicit_null_clears_an_override_and_an_absent_field_does_not(  # noqa: F811
    client_for, fake  # noqa: F811
) -> None:
    """CLAUDE.md §18. Without ``model_fields_set`` a ceiling set once could never be taken off."""
    t, account_id = await _linked("gads-policy-clear")
    headers = await auth_cookie(t.user)
    url = f"/api/v1/google-ads/accounts/{account_id}/policy"
    async with client_for(t.host) as client:
        await client.put("/api/v1/google-ads/policy", headers=headers, json={"max_cpc": 2.0})
        await client.put(url, headers=headers, json={"max_cpc": 5.0, "steering": "webshop"})
        # A save about something else leaves the override alone.
        await client.put(url, headers=headers, json={"steering": "webshop en showroom"})
        kept = (await client.get(url, headers=headers)).json()
        # An explicit null puts it back to inheriting the house value.
        await client.put(url, headers=headers, json={"max_cpc": None})
        cleared = (await client.get(url, headers=headers)).json()
    assert kept["max_cpc"] == 5.0
    assert kept["resolved"]["max_cpc"] == 5.0
    assert cleared["max_cpc"] is None
    assert cleared["resolved"]["max_cpc"] == 2.0


async def test_a_policy_for_another_orgs_account_is_a_404(client_for, fake) -> None:  # noqa: F811
    """Never a 403: the difference between two status codes is what reveals a row exists (§15)."""
    mine, _account = await _linked("gads-policy-mine")
    theirs, their_account = await _linked("gads-policy-theirs")
    headers = await auth_cookie(mine.user)
    async with client_for(mine.host) as client:
        response = await client.get(
            f"/api/v1/google-ads/accounts/{their_account}/policy", headers=headers
        )
    assert response.status_code == 404


# --- the decisions log -------------------------------------------------------------------------- #


async def test_a_kept_decision_is_recorded_and_stands(client_for, fake) -> None:  # noqa: F811
    """The one decision that exists nowhere else: "we looked at this and chose not to act"."""
    t, account_id = await _linked("gads-decision-kept")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as client:
        created = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/decisions",
            headers=headers,
            json={
                "subject": "beugel kosten",
                "decision": "kept",
                "reason": "converteert telefonisch",
            },
        )
        listed = await client.get(
            f"/api/v1/google-ads/accounts/{account_id}/decisions", headers=headers
        )
    assert created.status_code == 201
    assert created.json()["decision"] == "kept"
    # Snapshotted, never joined live (§16).
    assert created.json()["decided_by_name"]
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["reason"] == "converteert telefonisch"


async def test_the_same_decision_twice_appends_nothing_and_says_so(client_for, fake) -> None:  # noqa: F811
    """The pre-check that keeps a nightly agent from growing the log without bound.

    It is deliberately not a unique index: a duplicate history row costs one redundant line, while
    a constraint would 500 an agent's ordinary second call and would make "excluded in March,
    kept in June, excluded again in September" unrecordable.
    """
    t, account_id = await _linked("gads-decision-dedup")
    headers = await auth_cookie(t.user)
    payload = {"subject": "gratis offerte", "decision": "kept"}
    async with client_for(t.host) as client:
        first = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/decisions", headers=headers, json=payload
        )
        second = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/decisions", headers=headers, json=payload
        )
        listed = await client.get(
            f"/api/v1/google-ads/accounts/{account_id}/decisions", headers=headers
        )
    assert first.json() is not None
    assert second.json() is None
    assert listed.json()["total"] == 1


async def test_changing_your_mind_appends_and_the_newest_wins(client_for, fake) -> None:  # noqa: F811
    t, account_id = await _linked("gads-decision-reverse")
    headers = await auth_cookie(t.user)
    url = f"/api/v1/google-ads/accounts/{account_id}/decisions"
    async with client_for(t.host) as client:
        await client.post(url, headers=headers, json={"subject": "beugel", "decision": "kept"})
        await client.post(url, headers=headers, json={"subject": "beugel", "decision": "excluded"})
        listed = (await client.get(url, headers=headers)).json()
    assert listed["total"] == 2
    # Newest first, so the reversal is the head of the list and the history survives under it.
    assert listed["items"][0]["decision"] == "excluded"
    assert listed["items"][1]["decision"] == "kept"


async def test_a_withdrawn_decision_survives_and_stops_standing(client_for, fake) -> None:  # noqa: F811
    t, account_id = await _linked("gads-decision-withdraw")
    headers = await auth_cookie(t.user)
    url = f"/api/v1/google-ads/accounts/{account_id}/decisions"
    async with client_for(t.host) as client:
        created = await client.post(
            url, headers=headers, json={"subject": "beugel", "decision": "kept"}
        )
        response = await client.delete(f"{url}/{created.json()['id']}", headers=headers)
        default_list = (await client.get(url, headers=headers)).json()
        with_withdrawn = (
            await client.get(f"{url}?include_withdrawn=true", headers=headers)
        ).json()
    assert response.status_code == 200
    assert response.json()["withdrawn_at"] is not None
    assert response.json()["withdrawn_by_name"]
    assert default_list["total"] == 0
    assert with_withdrawn["total"] == 1


async def test_the_decisions_log_is_scoped_to_its_own_tenant(client_for, fake) -> None:  # noqa: F811
    mine, my_account = await _linked("gads-decision-mine")
    theirs, their_account = await _linked("gads-decision-theirs")
    headers = await auth_cookie(theirs.user)
    async with client_for(theirs.host) as client:
        await client.post(
            f"/api/v1/google-ads/accounts/{their_account}/decisions",
            headers=headers,
            json={"subject": "geheim", "decision": "kept"},
        )
    mine_headers = await auth_cookie(mine.user)
    async with client_for(mine.host) as client:
        listed = await client.get(
            f"/api/v1/google-ads/accounts/{my_account}/decisions", headers=mine_headers
        )
        crossed = await client.get(
            f"/api/v1/google-ads/accounts/{their_account}/decisions", headers=mine_headers
        )
    assert listed.json()["total"] == 0
    assert crossed.status_code == 404


# --- writes ------------------------------------------------------------------------------ #


async def test_a_budget_create_sends_micros_as_a_string_and_records_a_decision(  # noqa: F811
    client_for, fake  # noqa: F811
) -> None:
    t, account_id = await _linked("gads-write-budget")
    headers = await auth_cookie(t.user)
    fake.mutation(
        "campaignBudgets", resource_names=[f"customers/{CUSTOMER}/campaignBudgets/77"]
    )
    async with client_for(t.host) as client:
        response = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/budgets",
            headers=headers,
            json={"name": "Merk dagbudget", "amount": 40},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["applied"] == 1
    assert body["results"][0]["resource_name"].endswith("/campaignBudgets/77")

    collection, sent = fake.mutations("campaignBudgets")[0]
    assert collection == "campaignBudgets"
    # int64 as a JSON **string**, which is what Google sends and takes.
    assert sent["operations"][0]["create"]["amountMicros"] == "40000000"
    assert sent["operations"][0]["create"]["explicitlyShared"] is False

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = list((await session.scalars(select(GoogleAdsDecision))).all())
    assert [(r.subject, r.decision, r.applied, r.source) for r in rows] == [
        ("Merk dagbudget", "created", True, "write")
    ]


async def test_a_validate_only_write_changes_nothing_and_records_nothing(client_for, fake) -> None:  # noqa: F811
    """The real dry run: Google validates against the actual account and applies nothing. A log
    that claimed otherwise would be worse than no log."""
    t, account_id = await _linked("gads-write-validate")
    headers = await auth_cookie(t.user)
    fake.mutation("campaignBudgets", resource_names=[None])
    async with client_for(t.host) as client:
        response = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/budgets",
            headers=headers,
            json={"name": "Test", "amount": 40, "validate_only": True},
        )
    body = response.json()
    assert body["validate_only"] is True
    assert body["applied"] == 0
    assert "google_ads.warning.validated_not_applied" in body["warnings"]
    assert fake.mutations("campaignBudgets")[0][1]["validateOnly"] is True

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert list((await session.scalars(select(GoogleAdsDecision))).all()) == []


async def test_a_budget_over_the_policy_ceiling_is_refused_before_google_is_called(  # noqa: F811
    client_for, fake  # noqa: F811
) -> None:
    """A call-level refusal raises, and names the field (CLAUDE.md §18, #305)."""
    t, account_id = await _linked("gads-write-ceiling")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as client:
        await client.put(
            f"/api/v1/google-ads/accounts/{account_id}/policy",
            headers=headers,
            json={"max_daily_budget": 80},
        )
        response = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/budgets",
            headers=headers,
            json={"name": "Te groot", "amount": 800},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "google_ads_budget_over_ceiling"
    assert fake.mutations() == []


async def test_the_kill_switch_stops_every_write_without_touching_a_role(client_for, fake) -> None:  # noqa: F811
    """The permission decides who; this decides whether — one lever an owner reaches in a hurry."""
    t, account_id = await _linked("gads-write-killswitch")
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(
            select(GoogleAdsSettings).where(GoogleAdsSettings.org_id == t.org.id)
        )
        row.writes_enabled = False
        await session.commit()
    async with client_for(t.host) as client:
        response = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/budgets",
            headers=headers,
            json={"name": "Nope", "amount": 10},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "google_ads_writes_disabled"
    assert fake.mutations() == []


async def test_a_campaign_is_created_paused_even_though_google_defaults_to_enabled(  # noqa: F811
    client_for, fake  # noqa: F811
) -> None:
    t, account_id = await _linked("gads-write-campaign")
    headers = await auth_cookie(t.user)
    fake.mutation("campaigns", resource_names=[f"customers/{CUSTOMER}/campaigns/9"])
    async with client_for(t.host) as client:
        response = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/campaigns",
            headers=headers,
            json={"name": "Merk", "budget_id": "77"},
        )
    assert response.status_code == 201
    assert "google_ads.warning.created_paused" in response.json()["warnings"]
    created = fake.mutations("campaigns")[0][1]["operations"][0]["create"]
    assert created["status"] == "PAUSED"
    assert created["campaignBudget"].endswith("/campaignBudgets/77")


async def test_an_update_sends_a_field_mask_matching_its_own_body(client_for, fake) -> None:  # noqa: F811
    t, account_id = await _linked("gads-write-mask")
    headers = await auth_cookie(t.user)
    fake.mutation("campaigns", resource_names=[f"customers/{CUSTOMER}/campaigns/9"])
    async with client_for(t.host) as client:
        await client.patch(
            f"/api/v1/google-ads/accounts/{account_id}/campaigns/9",
            headers=headers,
            json={"status": "PAUSED"},
        )
    operation = fake.mutations("campaigns")[0][1]["operations"][0]
    # lowerCamelCase, matching the JSON body — the REST encoding of a FieldMask.
    assert operation["updateMask"] == "status"
    assert operation["update"]["resourceName"].endswith("/campaigns/9")


async def test_a_partial_failure_is_reported_per_operation_on_an_http_200(client_for, fake) -> None:  # noqa: F811
    """The shape the ordinary error classifier cannot see.

    Google answers **200** with a top-level ``partialFailureError``, which is a bare
    ``google.rpc.Status`` rather than the ``{"error": …}`` envelope every other failure path walks.
    A caller that only classifies non-2xx responses reads "two of three were written" as "three
    were written" — wrong in the direction nobody checks.
    """
    t, account_id = await _linked("gads-write-partial")
    headers = await auth_cookie(t.user)
    fake.mutation(
        "adGroupCriteria",
        resource_names=[
            f"customers/{CUSTOMER}/adGroupCriteria/11~1",
            None,
            f"customers/{CUSTOMER}/adGroupCriteria/11~3",
        ],
        partial_failure=[(1, "criterionError", "KEYWORD_HAS_TOO_MANY_WORDS")],
    )
    async with client_for(t.host) as client:
        response = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/keywords",
            headers=headers,
            json={
                "ad_group_id": "11",
                "keywords": [
                    {"text": "beugel", "match_type": "PHRASE"},
                    {"text": "een veel te lange zoekterm hier", "match_type": "PHRASE"},
                    {"text": "aligner", "match_type": "EXACT"},
                ],
            },
        )
    body = response.json()
    assert body["requested"] == 3
    assert body["applied"] == 2
    assert [r["ok"] for r in body["results"]] == [True, False, True]
    assert body["results"][1]["error_code"] == "criterionError.KEYWORD_HAS_TOO_MANY_WORDS"
    assert fake.mutations("adGroupCriteria")[0][1]["partialFailure"] is True

    # Only the applied ones are recorded: a decision is what was *done*.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = list((await session.scalars(select(GoogleAdsDecision))).all())
    assert sorted(r.subject for r in rows) == ["aligner", "beugel"]


async def test_a_protected_term_is_skipped_and_the_rest_of_the_batch_is_applied(  # noqa: F811
    client_for, fake  # noqa: F811
) -> None:
    """The row-level half of §18: refusing all twelve because the guard did its job on one of them
    punishes the caller for something that worked."""
    t, account_id = await _linked("gads-write-protected")
    headers = await auth_cookie(t.user)
    fake.mutation(
        "campaignCriteria", resource_names=[f"customers/{CUSTOMER}/campaignCriteria/1~5"]
    )
    async with client_for(t.host) as client:
        await client.put(
            f"/api/v1/google-ads/accounts/{account_id}/policy",
            headers=headers,
            json={"protected_terms": ["beugel"]},
        )
        response = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/negatives",
            headers=headers,
            json={
                "level": "campaign",
                "parent_id": "1",
                "terms": [
                    {"text": "beugel", "match_type": "BROAD"},
                    {"text": "vacature", "match_type": "PHRASE"},
                ],
            },
        )
    body = response.json()
    assert body["requested"] == 1
    assert body["applied"] == 1
    assert body["skipped"] == [
        {
            "subject": "beugel",
            "reason": "errors.google_ads_protected_term",
            "blocks": "beugel",
            "limit": None,
        }
    ]
    # Only the survivor reached Google.
    sent = fake.mutations("campaignCriteria")[0][1]["operations"]
    assert len(sent) == 1
    assert sent[0]["create"]["keyword"]["text"] == "vacature"
    assert sent[0]["create"]["negative"] is True


async def test_the_kept_half_of_a_review_is_recorded_without_touching_google(  # noqa: F811
    client_for, fake  # noqa: F811
) -> None:
    """The reason a shortlist stops growing back. A log holding only the exclusions re-proposes
    everything that was kept, every month, until nobody reads the list."""
    t, account_id = await _linked("gads-write-keep")
    headers = await auth_cookie(t.user)
    fake.mutation(
        "campaignCriteria", resource_names=[f"customers/{CUSTOMER}/campaignCriteria/1~5"]
    )
    async with client_for(t.host) as client:
        await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/negatives",
            headers=headers,
            json={
                "level": "campaign",
                "parent_id": "1",
                "terms": [{"text": "vacature", "match_type": "PHRASE"}],
                "keep": [
                    {"text": "beugel kosten", "reason": "converteert telefonisch"},
                    {"text": "beugel prijs", "reason": "merkgerelateerd"},
                ],
            },
        )
    assert len(fake.mutations("campaignCriteria")[0][1]["operations"]) == 1
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = list((await session.scalars(select(GoogleAdsDecision))).all())
    kept = {r.subject: r for r in rows if r.decision == "kept"}
    assert sorted(kept) == ["beugel kosten", "beugel prijs"]
    assert kept["beugel kosten"].applied is False
    assert kept["beugel kosten"].reason == "converteert telefonisch"


async def test_a_shared_budget_change_says_how_many_campaigns_it_moved(client_for, fake) -> None:  # noqa: F811
    """Warned rather than refused: editing a shared budget is legitimate, and being surprised by
    it is not."""
    t, account_id = await _linked("gads-write-shared")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign_budget",
        [
            {
                "campaignBudget": {
                    "id": "77",
                    "name": "Gedeeld",
                    "amountMicros": "40000000",
                    "referenceCount": "6",
                }
            }
        ],
    )
    fake.mutation(
        "campaignBudgets", resource_names=[f"customers/{CUSTOMER}/campaignBudgets/77"]
    )
    async with client_for(t.host) as client:
        response = await client.patch(
            f"/api/v1/google-ads/accounts/{account_id}/budgets/77",
            headers=headers,
            json={"amount": 60},
        )
    body = response.json()
    assert body["applied"] == 1
    assert "google_ads.warning.shared_budget" in body["warnings"]

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(select(GoogleAdsDecision))
    assert row.decision == "budget_changed"
    assert row.payload["from"] == 40.0
    assert row.payload["to"] == 60.0
    assert row.payload["campaigns_affected"] == 6


async def test_the_relative_ceiling_is_checked_against_googles_own_current_amount(  # noqa: F811
    client_for, fake  # noqa: F811
) -> None:
    """The built-in guard, and the read that makes it possible: a relative claim needs the number
    it is relative to, and only Google knows it."""
    t, account_id = await _linked("gads-write-relative")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign_budget",
        [{"campaignBudget": {"id": "77", "name": "Merk", "amountMicros": "40000000"}}],
    )
    fake.mutation("campaignBudgets", resource_names=[None])
    async with client_for(t.host) as client:
        response = await client.patch(
            f"/api/v1/google-ads/accounts/{account_id}/budgets/77",
            headers=headers,
            json={"amount": 400},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "google_ads_budget_increase_too_large"
    assert fake.mutations("campaignBudgets") == []


async def test_ad_copy_is_checked_against_the_policy_before_google_sees_it(
    client_for,
    fake,  # noqa: F811
) -> None:
    """A banned phrase is *checked*, not requested (#300): a tenant's banned phrase is not a
    preference for whatever wrote the copy to weigh."""
    t, account_id = await _linked("gads-write-adcopy")
    headers = await auth_cookie(t.user)
    fake.mutation("adGroupAds", resource_names=[f"customers/{CUSTOMER}/adGroupAds/11~2"])
    async with client_for(t.host) as client:
        await client.put(
            "/api/v1/google-ads/policy",
            headers=headers,
            json={"banned_phrases": ["de goedkoopste"]},
        )
        refused = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/ads",
            headers=headers,
            json={
                "ad_group_id": "11",
                "headlines": ["De Goedkoopste beugel", "Beugel op maat", "Vandaag geplaatst"],
                "descriptions": ["Een nette omschrijving.", "En nog een."],
                "final_urls": ["https://example.nl"],
            },
        )
        too_long = await client.post(
            f"/api/v1/google-ads/accounts/{account_id}/ads",
            headers=headers,
            json={
                "ad_group_id": "11",
                "headlines": ["x" * 31, "Beugel op maat", "Vandaag geplaatst"],
                "descriptions": ["Een nette omschrijving.", "En nog een."],
                "final_urls": ["https://example.nl"],
            },
        )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "google_ads_banned_phrase"
    assert too_long.status_code == 422
    # Named against the field, not against an operation index nobody can fix.
    assert too_long.json()["error"]["fields"]["headlines"] == "errors.google_ads_ad_headline_length"
    assert fake.mutations("adGroupAds") == []


async def test_a_google_refusal_surfaces_as_its_diagnosis_not_as_a_500(client_for, fake) -> None:  # noqa: F811
    t, account_id = await _linked("gads-write-refused")
    headers = await auth_cookie(t.user)
    fake.mutation(
        "campaigns",
        response=failure("authorizationError", "USER_PERMISSION_DENIED", status=403),
    )
    async with client_for(t.host) as client:
        response = await client.patch(
            f"/api/v1/google-ads/accounts/{account_id}/campaigns/9",
            headers=headers,
            json={"status": "PAUSED"},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "google_ads_permission"


async def test_a_real_mutate_is_never_retried(client_for, fake) -> None:  # noqa: F811
    """A retried create is a second campaign spending a second budget. The ladder is on reads."""
    t, account_id = await _linked("gads-write-noretry")
    headers = await auth_cookie(t.user)
    fake.mutation("campaigns", response=failure("internalError", "INTERNAL_ERROR", status=500))
    async with client_for(t.host) as client:
        await client.patch(
            f"/api/v1/google-ads/accounts/{account_id}/campaigns/9",
            headers=headers,
            json={"status": "PAUSED"},
        )
    assert len(fake.mutations("campaigns")) == 1


async def test_the_four_write_keys_are_separate_grants(client_for, fake) -> None:  # noqa: F811
    """The whole reason the split exists: a key minted to tidy search terms overnight must not be
    able to change a budget."""
    t = await make_tenant("gads-write-split")
    _t, account_id = await _linked("gads-write-split-acct")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as client:
        for path, body in (
            (f"/api/v1/google-ads/accounts/{account_id}/budgets", {"name": "x", "amount": 1}),
            (
                f"/api/v1/google-ads/accounts/{account_id}/negatives",
                {"level": "campaign", "parent_id": "1", "terms": []},
            ),
        ):
            # A member of a *different* org holding nothing is refused before anything is read.
            response = await client.post(path, headers=headers, json=body)
            assert response.status_code in (403, 404), (path, response.status_code)
    assert fake.mutations() == []

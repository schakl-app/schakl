"""The Ads policy and the mutate builders — pure logic, no database and no network.

Everything here is a function over dictionaries, which is why it can be exhaustive: the
protected-terms check has three match types and four shapes each, and asserting all twelve costs
nothing. That is the whole reason :mod:`app.modules.google_ads.policy` and
:mod:`~app.modules.google_ads.mutations` hold no session and no client.

The shapes are checked against the **v25 REST discovery document** (revision ``20260721``), not
against what a plausible request would look like. Three of them are not what a guess produces: a
new campaign defaults to ENABLED at Google, ``updateMask`` is lowerCamelCase in REST, and money is
an int64 that JSON carries as a string.
"""

from __future__ import annotations

import pytest

from app.modules.google_ads import mutations as ops
from app.modules.google_ads import policy as policy_rules

# --- resolution ------------------------------------------------------------------------------- #


class Row:
    """A stand-in for a policy row — the resolver reads attributes, never a model."""

    def __init__(self, **values: object) -> None:
        for key, value in values.items():
            setattr(self, key, value)


def test_no_rows_at_all_resolves_to_the_built_in_defaults() -> None:
    """The posture an existing install upgrades into: nothing new is refused except a budget
    change larger than the built-in relative ceiling."""
    resolved = policy_rules.resolve(None, None)
    assert resolved.protected_terms == ()
    assert resolved.max_daily_budget is None
    assert resolved.max_cpc is None
    assert resolved.max_budget_increase == 1.0


def test_lists_union_across_the_layers_and_scalars_inherit() -> None:
    """The rule that makes a house policy worth having: an account adds to it, never replaces it.

    A house exclusion list an account could silently override is a list nobody can rely on — and
    the failure would be invisible, because the account's own list still looks right on screen.
    """
    house = Row(always_exclude=["vacature", "wikipedia"], max_cpc=2.0, waste_min_cost=5.0)
    own = Row(always_exclude=["stage"], max_cpc=None)
    resolved = policy_rules.resolve(own, house)
    assert resolved.always_exclude == ("vacature", "wikipedia", "stage")
    assert resolved.max_cpc == 2.0
    assert resolved.waste_min_cost == 5.0


def test_an_account_scalar_beats_the_house_and_a_null_falls_back() -> None:
    house = Row(max_daily_budget=100.0)
    assert policy_rules.resolve(Row(max_daily_budget=250.0), house).max_daily_budget == 250.0
    assert policy_rules.resolve(Row(max_daily_budget=None), house).max_daily_budget == 100.0


def test_prose_stays_in_two_fields_and_is_never_concatenated() -> None:
    """#300's rule. Fused, "we never bid on competitor names" and "this client sells competitor
    parts" become one contradictory instruction and a model obeys whichever it read last."""
    resolved = policy_rules.resolve(
        Row(steering="Stuur op de webshop."), Row(steering="Nooit op merknamen van concurrenten.")
    )
    assert resolved.house_steering == "Nooit op merknamen van concurrenten."
    assert resolved.account_steering == "Stuur op de webshop."
    payload = resolved.as_payload()
    assert payload["agency_steering"] != payload["account_steering"]


def test_terms_are_normalised_and_deduplicated_on_the_way_out() -> None:
    resolved = policy_rules.resolve(
        Row(protected_terms=["Beugel", "  beugel  ", "Onzichtbare Beugel"]), None
    )
    assert resolved.protected_terms == ("beugel", "onzichtbare beugel")


def test_a_nonsense_stored_ceiling_is_clamped_rather_than_enforced() -> None:
    """The clamps run last, over whatever the layers produced. A negative ceiling would refuse
    every write and read to a tenant as the integration being broken."""
    resolved = policy_rules.resolve(Row(max_daily_budget=-50.0, max_cpc=-1.0), None)
    assert resolved.max_daily_budget == 0.0
    assert resolved.max_cpc == 0.0


def test_an_over_long_list_is_cut_and_reported() -> None:
    resolved = policy_rules.resolve(Row(protected_terms=[f"term {n}" for n in range(600)]), None)
    assert len(resolved.protected_terms) == policy_rules.MAX_TERMS
    assert "google_ads.warning.policy_terms_capped" in resolved.warnings


# --- would this exclusion actually block a protected term? ------------------------------- #


@pytest.mark.parametrize(
    ("negative", "match_type", "protected", "expected"),
    [
        # EXACT blocks only the identical term.
        ("beugel", "EXACT", "beugel", True),
        ("beugel kosten", "EXACT", "beugel", False),
        ("beugel", "EXACT", "beugel kosten", False),
        # PHRASE blocks a term containing the words in order and adjacent.
        ("beugel", "PHRASE", "onzichtbare beugel amsterdam", True),
        ("beugel amsterdam", "PHRASE", "onzichtbare beugel amsterdam", True),
        ("amsterdam beugel", "PHRASE", "onzichtbare beugel amsterdam", False),
        # BROAD blocks a term containing all the words, in any order.
        ("amsterdam beugel", "BROAD", "onzichtbare beugel amsterdam", True),
        ("beugel rotterdam", "BROAD", "onzichtbare beugel amsterdam", False),
        # An unknown match type is treated as the widest, so the failure direction is a refusal
        # rather than a client's brand silently going dark.
        ("amsterdam beugel", None, "onzichtbare beugel amsterdam", True),
    ],
)
def test_blocks_models_googles_own_matching(
    negative: str, match_type: str | None, protected: str, expected: bool
) -> None:
    """The check that decides whether the guard is trusted or switched off.

    A naive version refuses any exclusion *containing* a protected word, and it is wrong in the
    direction that matters: an EXACT negative on "beugel kosten" cannot stop "beugel" from
    serving, so refusing it teaches an agency that the guard cries wolf.
    """
    assert policy_rules.blocks(negative, match_type, protected) is expected


def test_protected_hit_names_the_term_it_would_block() -> None:
    """Named rather than implied: "refused" invites an argument with the software, "would also
    block *beugel*" invites a fix."""
    resolved = policy_rules.resolve(Row(protected_terms=["beugel", "aligner"]), None)
    assert policy_rules.protected_hit(resolved, "beugel", "BROAD") == "beugel"
    assert policy_rules.protected_hit(resolved, "aligner kosten", "BROAD") is None
    assert policy_rules.protected_hit(resolved, "tandarts", "BROAD") is None


def test_a_narrower_exclusion_than_the_protected_term_is_allowed() -> None:
    """The case the naive check gets wrong, and the reason it would get the guard switched off.

    "beugel" is protected. Excluding "goedkope beugel" BROAD does **not** stop "beugel" from
    serving — Google's broad negative needs *both* words present — so refusing it would be the
    guard crying wolf on a perfectly sensible exclusion. Excluding "beugel" itself does block it,
    and is refused.
    """
    resolved = policy_rules.resolve(Row(protected_terms=["beugel"]), None)
    assert policy_rules.protected_hit(resolved, "goedkope beugel", "BROAD") is None
    assert policy_rules.protected_hit(resolved, "beugel", "BROAD") == "beugel"


def test_a_broad_exclusion_that_swallows_a_long_tail_protected_term_is_refused() -> None:
    """And the direction that must never be missed: a one-word broad negative takes the whole
    long tail with it, including the phrase the agency wrote down as untouchable."""
    resolved = policy_rules.resolve(Row(protected_terms=["goedkope beugel amsterdam"]), None)
    assert policy_rules.protected_hit(resolved, "beugel", "BROAD") == "goedkope beugel amsterdam"
    assert policy_rules.protected_hit(resolved, "beugel", "EXACT") is None


def test_a_banned_phrase_is_caught_through_casing_and_doubled_spaces() -> None:
    resolved = policy_rules.resolve(Row(banned_phrases=["de goedkoopste"]), None)
    assert policy_rules.banned_hit(resolved, "Nu De  Goedkoopste van Nederland") == "de goedkoopste"
    assert policy_rules.banned_hit(resolved, "Scherp geprijsd") is None


# --- the ceilings ------------------------------------------------------------------------------- #


def test_a_budget_over_the_absolute_ceiling_is_refused_and_names_the_limit() -> None:
    resolved = policy_rules.resolve(Row(max_daily_budget=80.0), None)
    refusal = policy_rules.budget_refusal(resolved, amount=800.0, previous=40.0)
    assert refusal is not None
    assert refusal.key == "errors.google_ads_budget_over_ceiling"
    assert refusal.limit == 80.0


def test_the_relative_ceiling_catches_an_extra_zero_and_permits_a_seasonal_change() -> None:
    """The built-in guard, and the reason it is relative: it needs no knowledge of the account."""
    resolved = policy_rules.resolve(None, None)  # built-in: may at most double
    assert policy_rules.budget_refusal(resolved, amount=400.0, previous=40.0) is not None
    assert policy_rules.budget_refusal(resolved, amount=70.0, previous=40.0) is None


def test_a_decrease_is_never_refused() -> None:
    """An agency reacting to an overspend at five on a Friday must not be arguing with us."""
    resolved = policy_rules.resolve(Row(max_daily_budget=80.0), None)
    assert policy_rules.budget_refusal(resolved, amount=10.0, previous=1_000.0) is None


def test_a_create_has_no_previous_amount_so_only_the_absolute_ceiling_binds_it() -> None:
    """Stated in a test because it is the gap somebody would otherwise find the hard way."""
    built_in = policy_rules.resolve(None, None)
    assert policy_rules.budget_refusal(built_in, amount=10_000.0, previous=None) is None
    capped = policy_rules.resolve(Row(max_daily_budget=80.0), None)
    assert policy_rules.budget_refusal(capped, amount=10_000.0, previous=None) is not None


def test_a_bid_over_the_ceiling_is_refused() -> None:
    resolved = policy_rules.resolve(Row(max_cpc=2.5), None)
    assert policy_rules.cpc_refusal(resolved, amount=4.0) is not None
    assert policy_rules.cpc_refusal(resolved, amount=2.5) is None
    assert policy_rules.cpc_refusal(resolved, amount=None) is None


# --- the operation shapes ----------------------------------------------------------------- #


def test_money_is_micros_carried_as_a_string() -> None:
    """Sent as a number it is accepted for small values and silently loses precision in the range
    a real budget lives in."""
    assert ops.to_micros(40) == "40000000"
    assert ops.to_micros(12.34) == "12340000"
    assert isinstance(ops.to_micros(40), str)


def test_update_mask_is_derived_from_the_body_and_excludes_the_resource_name() -> None:
    """A hand-written mask and a hand-written body are two spellings of one list; the day they
    disagree Google applies the intersection and reports success."""
    operation = ops.operation_update("customers/1/campaignBudgets/2", {"amountMicros": "50000000"})
    assert operation["updateMask"] == "amountMicros"
    assert operation["update"]["resourceName"] == "customers/1/campaignBudgets/2"
    assert "resourceName" not in operation["updateMask"]


def test_an_absent_field_is_dropped_from_both_the_body_and_the_mask() -> None:
    operation = ops.operation_update("customers/1/adGroups/2", {"status": "PAUSED", "name": None})
    assert operation["updateMask"] == "status"
    assert "name" not in operation["update"]


def test_an_update_with_nothing_to_change_refuses_rather_than_sending_an_empty_mask() -> None:
    with pytest.raises(ValueError, match="empty field mask"):
        ops.operation_update("customers/1/campaigns/2", {"status": None})


def test_a_new_campaign_is_paused_because_googles_own_default_is_enabled() -> None:
    """The discovery document says so in as many words, so "create it paused" is a field we set
    rather than the absence of a decision."""
    resource = ops.campaign_create(name="Merk", budget_resource="customers/1/campaignBudgets/2")
    assert resource["status"] == "PAUSED"
    assert resource["campaignBudget"] == "customers/1/campaignBudgets/2"
    # A Search campaign quietly opted into Display spends its budget where nobody is looking.
    assert resource["networkSettings"]["targetContentNetwork"] is False


def test_a_new_ad_group_and_a_new_ad_are_paused_too() -> None:
    assert ops.ad_group_create(name="Merk", campaign_resource="c")["status"] == "PAUSED"
    ad = ops.responsive_search_ad_create(
        ad_group_resource="ag",
        headlines=["a", "b", "c"],
        descriptions=["d", "e"],
        final_urls=["https://example.nl"],
    )
    assert ad["status"] == "PAUSED"


def test_a_budget_create_says_whether_it_is_shared_instead_of_letting_google_default_it() -> None:
    """Google defaults ``explicitlyShared`` to true, and a shared budget's next edit moves every
    campaign attached to it."""
    assert ops.budget_create(name="b", amount=40)["explicitlyShared"] is False
    assert ops.budget_create(name="b", amount=40, shared=True)["explicitlyShared"] is True


def test_a_composite_resource_name_joins_with_a_tilde() -> None:
    assert (
        ops.ad_group_criterion_rn("123-456-7890", 11, 22)
        == "customers/1234567890/adGroupCriteria/11~22"
    )
    assert ops.campaign_criterion_rn("1234567890", 1, 2) == (
        "customers/1234567890/campaignCriteria/1~2"
    )


def test_only_the_named_collections_may_be_written() -> None:
    """A closed list rather than interpolation: the resource is the only part of a mutate path
    that is not a validated id."""
    assert ops.check_resource("campaignBudgets") == "campaignBudgets"
    with pytest.raises(ValueError, match="not a writable"):
        ops.check_resource("customers")


@pytest.mark.parametrize(
    ("headlines", "descriptions", "urls", "field"),
    [
        (["a", "b"], ["d", "e"], ["https://x.nl"], "headlines"),
        (["a" * 31, "b", "c"], ["d", "e"], ["https://x.nl"], "headlines"),
        (["a", "b", "c"], ["d"], ["https://x.nl"], "descriptions"),
        (["a", "b", "c"], ["d", "e" * 91], ["https://x.nl"], "descriptions"),
        (["a", "b", "c"], ["d", "e"], ["example.nl"], "final_urls"),
    ],
)
def test_ad_copy_limits_are_reported_against_the_field_not_an_operation_index(
    headlines: list[str], descriptions: list[str], urls: list[str], field: str
) -> None:
    """#289's rule: a check the row report cannot name is a check the preview does not have.
    Google answers ``STRING_LENGTH_ERROR`` against an operation index, which nobody can fix."""
    assert field in ops.validate_ad_copy(headlines, descriptions, urls)


def test_valid_ad_copy_passes() -> None:
    assert ops.validate_ad_copy(["a", "b", "c"], ["d", "e"], ["https://x.nl"]) == {}

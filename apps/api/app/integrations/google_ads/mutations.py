"""Building the operations a ``:mutate`` call carries. Business-licensed — see LICENSE.

Pure functions over dictionaries: no client, no session, no account. That is deliberate, because
every one of the traps below is a *shape* mistake, and a shape mistake is only cheap to find when
the shape can be asserted without a network.

Written from the **v25 REST discovery document** (revision ``20260721``), not from memory —
CLAUDE.md §11's rule, and the one that already paid for itself three times on the read surface.
Five things in it are not what a plausible guess would produce:

* **``updateMask`` is lowerCamelCase in REST**, matching the JSON body rather than the proto:
  ``"amountMicros"``, not ``"amount_micros"``. It is a ``google.protobuf.FieldMask``, whose JSON
  encoding is defined as lowerCamelCase, and Google's own REST examples write it that way. So
  :func:`operation_update` **derives the mask from the body it is sending** rather than taking one
  — two spellings of the same field list cannot disagree if only one of them exists.
* **A new campaign defaults to ``ENABLED``.** The discovery document says so in as many words
  ("When a new campaign is added, the status defaults to ENABLED"), so "create it paused" is not
  the absence of a decision — it is a field we set, and :func:`campaign_create` sets it
  unconditionally.
* **``adGroupCriterion.negative`` is immutable**, and so is ``keyword``. Google's own error
  enum carries ``CANT_UPDATE_NEGATIVE`` for the attempt. A positive keyword cannot be turned into
  an exclusion, and an exclusion's text cannot be corrected: both are remove-then-add. Only
  ``status`` and ``cpcBidMicros`` are updatable, which is why there is no keyword-text update here.
* **``sharedCriterion`` and ``campaignSharedSet`` have no update operation at all** — create and
  remove only. A negative on a shared list is added or taken off; there is no edit.
* **Money is an int64, which JSON carries as a string.** Sent as a number it is accepted for
  small values and silently loses precision in the range a real budget lives in.

The resource names are built here too, because ``customers/{cid}/adGroupCriteria/{ag}~{crit}``
is a composite Google spells with a tilde and nothing else in the product does.
"""

from __future__ import annotations

from typing import Any

from app.core.googleads import normalise_customer_id

#: The ``:mutate`` collections this module may address. Its GAQL sibling
#: (``gaql.ALLOWED_RESOURCES``) governs reading; this governs writing, and the two are separate
#: lists on purpose — reading ``billing_setup`` is a privacy question, writing ``customer`` is a
#: different kind of act entirely. Anything not named here has no code path that reaches it, and
#: :func:`check_resource` is what makes that a refusal rather than a 404 from Google.
MUTABLE_RESOURCES = frozenset(
    {
        "campaigns",
        "campaignBudgets",
        "adGroups",
        "adGroupCriteria",
        "campaignCriteria",
        "adGroupAds",
        "sharedSets",
        "sharedCriteria",
        "campaignSharedSets",
    }
)

#: Google's own ceiling on one mutate request is 10 000 operations, but a batch that large from a
#: tool call is never a considered act — it is a loop that got away. The cap is ours and it is
#: low enough that the refusal arrives while somebody is still watching.
MAX_OPERATIONS = 200

#: Responsive search ads. Google's limits, and they are validated here rather than left to the
#: API because a 422 naming the headline that is two characters too long is a fixable answer and
#: ``STRING_LENGTH_ERROR`` on operation 3 is not.
MAX_HEADLINE_CHARS = 30
MAX_DESCRIPTION_CHARS = 90
MIN_HEADLINES = 3
MAX_HEADLINES = 15
MIN_DESCRIPTIONS = 2
MAX_DESCRIPTIONS = 4


def to_micros(amount: float) -> str:
    """A money amount as Google wants it: micros, int64, carried in JSON as a **string**.

    Not the inverse of ``reporting.money``, which rounds to two decimals for display and is
    lossy; this is the write direction and rounds only at the micro.
    """
    return str(int(round(float(amount) * 1_000_000)))


# --- resource names ---------------------------------------------------------------------------- #


def _cid(customer_id: str) -> str:
    return normalise_customer_id(customer_id)


def campaign_rn(customer_id: str, campaign_id: str | int) -> str:
    return f"customers/{_cid(customer_id)}/campaigns/{campaign_id}"


def budget_rn(customer_id: str, budget_id: str | int) -> str:
    return f"customers/{_cid(customer_id)}/campaignBudgets/{budget_id}"


def ad_group_rn(customer_id: str, ad_group_id: str | int) -> str:
    return f"customers/{_cid(customer_id)}/adGroups/{ad_group_id}"


def ad_group_criterion_rn(
    customer_id: str, ad_group_id: str | int, criterion_id: str | int
) -> str:
    """``…/adGroupCriteria/{ad_group_id}~{criterion_id}`` — a composite, joined by a tilde."""
    return f"customers/{_cid(customer_id)}/adGroupCriteria/{ad_group_id}~{criterion_id}"


def campaign_criterion_rn(
    customer_id: str, campaign_id: str | int, criterion_id: str | int
) -> str:
    return f"customers/{_cid(customer_id)}/campaignCriteria/{campaign_id}~{criterion_id}"


def shared_set_rn(customer_id: str, shared_set_id: str | int) -> str:
    return f"customers/{_cid(customer_id)}/sharedSets/{shared_set_id}"


def shared_criterion_rn(
    customer_id: str, shared_set_id: str | int, criterion_id: str | int
) -> str:
    return f"customers/{_cid(customer_id)}/sharedCriteria/{shared_set_id}~{criterion_id}"


def campaign_shared_set_rn(
    customer_id: str, campaign_id: str | int, shared_set_id: str | int
) -> str:
    return f"customers/{_cid(customer_id)}/campaignSharedSets/{campaign_id}~{shared_set_id}"


def ad_group_ad_rn(customer_id: str, ad_group_id: str | int, ad_id: str | int) -> str:
    return f"customers/{_cid(customer_id)}/adGroupAds/{ad_group_id}~{ad_id}"


# --- operations -------------------------------------------------------------------------------- #


def operation_create(resource: dict[str, Any]) -> dict[str, Any]:
    return {"create": resource}


def operation_remove(resource_name: str) -> dict[str, Any]:
    return {"remove": resource_name}


def operation_update(resource_name: str, fields: dict[str, Any]) -> dict[str, Any]:
    """An update, with its ``updateMask`` derived from the fields it actually carries.

    Deriving rather than accepting is the whole point: a hand-written mask and a hand-written
    body are two spellings of one list, and the day they disagree Google applies the intersection
    and reports success. ``resourceName`` is excluded because it identifies the row rather than
    being one of the values changed — naming it in the mask is ``FIELD_MASK_NOT_ALLOWED``.

    Absent fields are dropped before the mask is built, so "leave this alone" is expressed by not
    passing it and can never become "set this to null" (CLAUDE.md §18, one layer down).
    """
    body = {key: value for key, value in fields.items() if value is not None}
    if not body:
        raise ValueError("an update operation with no fields would send an empty field mask")
    return {
        "updateMask": ",".join(body),
        "update": {"resourceName": resource_name, **body},
    }


def check_resource(resource: str) -> str:
    """``resource`` if this module may write it, else a :class:`ValueError`.

    A closed list rather than string interpolation into the URL: the resource is the only part of
    a mutate path that is not a validated id, and a surface that can address any collection Google
    has is a surface whose blast radius is Google's roadmap rather than this module's.
    """
    if resource not in MUTABLE_RESOURCES:
        raise ValueError(f"{resource} is not a writable google ads resource")
    return resource


# --- the resources themselves ------------------------------------------------------------------ #


def keyword_info(text: str, match_type: str) -> dict[str, Any]:
    return {"text": text, "matchType": match_type.strip().upper()}


def budget_create(*, name: str, amount: float, shared: bool = False) -> dict[str, Any]:
    """A daily budget.

    ``explicitlyShared`` defaults to **true** at Google when a create omits it, and a shared
    budget is a budget whose next edit silently moves every campaign attached to it. So it is set
    here explicitly, and defaults to *not* shared: one budget per campaign is the arrangement
    where "raise this campaign's budget" means what it says.
    """
    return {
        "name": name,
        "amountMicros": to_micros(amount),
        "deliveryMethod": "STANDARD",
        "explicitlyShared": bool(shared),
    }


def campaign_create(
    *,
    name: str,
    budget_resource: str,
    channel: str = "SEARCH",
    target_google_search: bool = True,
    target_search_network: bool = True,
    target_content_network: bool = False,
    eu_political_advertising: bool = False,
) -> dict[str, Any]:
    """A campaign that is **paused from the moment it exists**.

    Google's default is ENABLED, so this is a decision rather than an omission: a campaign this
    module creates has no ad groups, no keywords and no ads for as long as it takes whoever asked
    for it to add them, and an enabled campaign in that state either spends nothing or spends on
    something nobody has reviewed. Somebody enables it deliberately, in Google's own interface or
    through the update route, and that act is in the trail.

    ``targetContentNetwork`` is off by default for the same reason it is the first thing an
    agency turns off by hand: a Search campaign quietly opted into Display spends its budget
    somewhere nobody was looking.

    ``containsEuPoliticalAdvertising`` is **required** on create in v25 — the EU political
    advertising regulation — and omitting it fails every create with ``fieldError: REQUIRED``
    naming a field no error message we surfaced ever mentioned. It is an argument rather than a
    constant because a political advertiser is a real client and answering for them is not ours
    to do; the default is the honest one for an agency that does not run those campaigns.
    """
    return {
        "name": name,
        "status": "PAUSED",
        "campaignBudget": budget_resource,
        "advertisingChannelType": channel.strip().upper(),
        "networkSettings": {
            "targetGoogleSearch": target_google_search,
            "targetSearchNetwork": target_search_network,
            "targetContentNetwork": target_content_network,
            "targetPartnerSearchNetwork": False,
        },
        "containsEuPoliticalAdvertising": (
            "CONTAINS_EU_POLITICAL_ADVERTISING"
            if eu_political_advertising
            else "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
        ),
        # A bidding strategy is required and Maximize Clicks is the only one that needs no
        # conversion history, which a campaign created five seconds ago does not have.
        "targetSpend": {},
    }


def ad_group_create(
    *, name: str, campaign_resource: str, cpc_bid: float | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "campaign": campaign_resource,
        "status": "PAUSED",
        "type": "SEARCH_STANDARD",
    }
    if cpc_bid is not None:
        out["cpcBidMicros"] = to_micros(cpc_bid)
    return out


def keyword_create(
    *, ad_group_resource: str, text: str, match_type: str, cpc_bid: float | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "adGroup": ad_group_resource,
        "status": "ENABLED",
        "keyword": keyword_info(text, match_type),
    }
    if cpc_bid is not None:
        out["cpcBidMicros"] = to_micros(cpc_bid)
    return out


def negative_keyword_create(
    *, parent_field: str, parent_resource: str, text: str, match_type: str
) -> dict[str, Any]:
    """An exclusion at ad-group or campaign level.

    ``negative: true`` is **immutable** at Google, which is what makes an exclusion a different
    object from a keyword rather than a keyword with a flag: there is no edit that turns one into
    the other, in either direction.
    """
    return {
        parent_field: parent_resource,
        "negative": True,
        "keyword": keyword_info(text, match_type),
    }


def shared_negative_create(
    *, shared_set_resource: str, text: str, match_type: str
) -> dict[str, Any]:
    """A member of a negative-keyword list. ``sharedCriterion`` has no update operation."""
    return {"sharedSet": shared_set_resource, "keyword": keyword_info(text, match_type)}


def shared_set_create(*, name: str) -> dict[str, Any]:
    return {"name": name, "type": "NEGATIVE_KEYWORDS"}


def campaign_shared_set_create(
    *, campaign_resource: str, shared_set_resource: str
) -> dict[str, Any]:
    return {"campaign": campaign_resource, "sharedSet": shared_set_resource}


def responsive_search_ad_create(
    *,
    ad_group_resource: str,
    headlines: list[str],
    descriptions: list[str],
    final_urls: list[str],
    path1: str | None = None,
    path2: str | None = None,
) -> dict[str, Any]:
    """A responsive search ad, created **paused**.

    Paused for the same reason a campaign is: an ad this module writes has not been read by
    anybody at the moment it is written, and an ad group's traffic starts reaching it the instant
    it is enabled. Reviewing it and turning it on are one deliberate act.

    The counts and lengths are Google's, checked here — see :func:`validate_ad_copy`.
    """
    return {
        "adGroup": ad_group_resource,
        "status": "PAUSED",
        "ad": {
            "finalUrls": list(final_urls),
            "responsiveSearchAd": {
                "headlines": [{"text": text} for text in headlines],
                "descriptions": [{"text": text} for text in descriptions],
                **({"path1": path1} if path1 else {}),
                **({"path2": path2} if path2 else {}),
            },
        },
    }


def validate_ad_copy(
    headlines: list[str], descriptions: list[str], final_urls: list[str]
) -> dict[str, str]:
    """Google's own limits, as a ``{field: i18n key}`` map the error envelope can carry.

    Checked before the call rather than after, because Google reports a length violation as
    ``STRING_LENGTH_ERROR`` against an operation index, and an index is not a field somebody can
    fix. Same rule #289 applied to spreadsheet imports: a check the row report cannot name is a
    check the preview does not have.
    """
    fields: dict[str, str] = {}
    if not (MIN_HEADLINES <= len(headlines) <= MAX_HEADLINES):
        fields["headlines"] = "errors.google_ads_ad_headline_count"
    if not (MIN_DESCRIPTIONS <= len(descriptions) <= MAX_DESCRIPTIONS):
        fields["descriptions"] = "errors.google_ads_ad_description_count"
    if any(len(text.strip()) == 0 or len(text) > MAX_HEADLINE_CHARS for text in headlines):
        fields["headlines"] = "errors.google_ads_ad_headline_length"
    if any(len(text.strip()) == 0 or len(text) > MAX_DESCRIPTION_CHARS for text in descriptions):
        fields["descriptions"] = "errors.google_ads_ad_description_length"
    if not final_urls or any(
        not url.lower().startswith(("http://", "https://")) for url in final_urls
    ):
        fields["final_urls"] = "errors.google_ads_ad_final_url"
    return fields

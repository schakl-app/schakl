"""The guardrails, as pure functions. Business-licensed — see LICENSE.

No session, no Meta call, no clock it is not handed: everything here takes values and returns
a verdict, so the write surface, the policy screen and a test all get the same answer.

**Three layers, and they must not fuse.** The built-in is code, the house policy is a row, an
account's policy is a row over that. Scalars *inherit* (``None`` means "whatever the layer
below says"), lists *union* (a phrase the house bans is banned on every account, whatever the
account adds).

**A default may only live in the layer that needs no local knowledge** (CLAUDE.md §10). The one
built-in ceiling is therefore *relative* — a budget may at most double in one change — because
that catches the extra zero on any account in any currency, while any absolute figure invented
in code would refuse a legitimate budget on one account and wave a mistake through on another.
The consequence is written down rather than left to be discovered: a budget **create** has no
previous amount, so it is bounded by the absolute ceilings alone, and where none is set it is
bounded by the permission alone. What makes that acceptable here is that everything is created
``PAUSED``, and activating is a permission of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

#: The EU member states, as the ISO codes Meta's targeting takes. Reading a country as "EU" is
#: what decides whether an ad set needs the two DSA names and whether a political ad is
#: refused, so the list is stated once.
EU_COUNTRIES: frozenset[str] = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
        "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    }
)  # fmt: skip

#: The special ad category Meta stopped serving in the EU in October 2025.
POLITICAL = "ISSUES_ELECTIONS_POLITICS"

#: How many times an ad set's budget may change in an hour. Meta's own limit (error 613 /
#: 1487632); refused here with the number rather than discovered there without one.
BUDGET_CHANGES_PER_HOUR = 4


@dataclass(frozen=True)
class AdsPolicy:
    """The effective policy for one ad account."""

    max_daily_budget: int | None = None
    max_lifetime_budget: int | None = None
    max_budget_increase: Decimal | None = None
    banned_phrases: tuple[str, ...] = ()
    dsa_beneficiary: str | None = None
    dsa_payor: str | None = None
    steering: str = ""
    house_steering: str = ""


BUILT_IN = AdsPolicy(max_budget_increase=Decimal("1.0"))


@dataclass(frozen=True)
class Refusal:
    """Why a write was refused: an i18n key, the field, and the numbers (§9, #305)."""

    code: str
    field: str
    details: dict[str, Any]


def resolve(own: Any | None, house: Any | None) -> AdsPolicy:
    """Built-in, then the house row, then the account's."""

    def scalar(name: str) -> Any:
        for layer in (own, house):
            value = getattr(layer, name, None) if layer is not None else None
            if value is not None and value != "":
                return value
        return getattr(BUILT_IN, name)

    phrases: list[str] = []
    for layer in (house, own):
        for phrase in (getattr(layer, "banned_phrases", None) or []) if layer is not None else []:
            clean = " ".join(str(phrase).split())
            if clean and clean.casefold() not in {p.casefold() for p in phrases}:
                phrases.append(clean)
    return AdsPolicy(
        max_daily_budget=scalar("max_daily_budget"),
        max_lifetime_budget=scalar("max_lifetime_budget"),
        max_budget_increase=scalar("max_budget_increase"),
        banned_phrases=tuple(phrases),
        dsa_beneficiary=scalar("dsa_beneficiary"),
        dsa_payor=scalar("dsa_payor"),
        # Prose is kept in two fields and never concatenated: which layer said it is part of
        # what it means.
        steering=str(getattr(own, "steering", "") or "") if own is not None else "",
        house_steering=str(getattr(house, "steering", "") or "") if house is not None else "",
    )


def budget_refusal(
    policy: AdsPolicy,
    *,
    kind: str,
    new: int | None,
    previous: int | None,
) -> Refusal | None:
    """Whether a budget may be set to ``new``. ``kind`` is ``daily`` or ``lifetime``.

    A decrease is never refused: every guardrail here exists to stop money being spent that
    nobody meant to spend, and lowering a budget cannot do that.
    """
    if new is None:
        return None
    field = f"{kind}_budget_cents"
    if new <= 0:
        return Refusal("errors.meta_ads_budget_invalid", field, {"value": new})
    if previous is not None and new <= previous:
        return None
    ceiling = policy.max_daily_budget if kind == "daily" else policy.max_lifetime_budget
    if ceiling is not None and new > ceiling:
        return Refusal(
            "errors.meta_ads_budget_over_ceiling", field, {"limit": ceiling, "value": new}
        )
    if previous and policy.max_budget_increase is not None:
        allowed = int(previous * (Decimal(1) + policy.max_budget_increase))
        if new > allowed:
            return Refusal(
                "errors.meta_ads_budget_increase_too_large",
                field,
                {"limit": allowed, "value": new, "previous": previous},
            )
    return None


def phrase_refusal(policy: AdsPolicy, texts: dict[str, str | None]) -> Refusal | None:
    """The first banned phrase any of ``texts`` contains, naming the field it was found in."""
    for field, text in texts.items():
        if not text:
            continue
        haystack = " ".join(text.split()).casefold()
        for phrase in policy.banned_phrases:
            needle = phrase.casefold()
            if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
                return Refusal("errors.meta_ads_banned_phrase", field, {"phrase": phrase})
    return None


def eu_countries(countries: list[str] | tuple[str, ...]) -> list[str]:
    return sorted({c.upper() for c in countries if c.upper() in EU_COUNTRIES})


def political_refusal(categories: list[str], countries: list[str]) -> Refusal | None:
    """Meta stopped political, electoral and social-issue ads in the EU (October 2025).

    Refused before the call: a campaign built, approved and activated only to be rejected by
    Meta's review is an afternoon of somebody's work, and the rule is not going to change by
    being asked.
    """
    if POLITICAL not in categories:
        return None
    inside = eu_countries(countries)
    if not inside:
        return None
    return Refusal("errors.meta_ads_political_eu", "special_ad_categories", {"countries": inside})


def dsa_names(
    *,
    requested_beneficiary: str | None,
    requested_payor: str | None,
    policy: AdsPolicy,
    account_beneficiary: str | None,
    account_payor: str | None,
    client_name: str | None,
) -> tuple[str | None, str | None]:
    """Who the ad is for and who pays for it, from the most specific statement there is.

    What the caller sent, then the policy, then the ad account's own defaults at Meta, then
    the client's **legal** name — the entity an invoice would be addressed to, which is what
    the Act asks for (``app.core.naming``). ``None`` where nothing says: the caller refuses
    rather than sends an ad set into the EU with a name nobody chose.
    """

    def first(*values: str | None) -> str | None:
        for value in values:
            clean = (value or "").strip()
            if clean:
                return clean[:512]
        return None

    return (
        first(requested_beneficiary, policy.dsa_beneficiary, account_beneficiary, client_name),
        first(requested_payor, policy.dsa_payor, account_payor, client_name),
    )

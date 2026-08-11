"""The Ads policy: three layers, resolved once, and the checks that give it teeth.

Business-licensed — see LICENSE.

**No database access lives here.** Everything is a pure function over two rows, which is what
lets the interesting half — "would this negative keyword stop a protected term from serving?" —
be tested exhaustively without a Postgres, and what stops the resolution rules from being
re-derived slightly differently at each call site.

## The three layers, and why they must not fuse

#300 established the shape for reporting and it is the same shape here: **product invariants are
code, the agency's standing rules are a row, and what is true about one advertiser is a row.**

* :data:`BUILT_IN` — what is true of Google Ads, not of any tenant. It carries exactly one
  substantive value (:data:`BUILT_IN.max_budget_increase`), and the choice of *which* guard is
  built in is the argument: a **relative** ceiling needs no knowledge of an account, so it can be
  defaulted honestly; an absolute one cannot, and a figure invented here would refuse a
  legitimate seasonal budget on one account while permitting an extra zero on another.
* the **house** row (``account_id IS NULL``) — the agency's own standing rules.
* the **account** row — this advertiser's.

Lists **union**, scalars **inherit**, prose **stays separate**. A house exclusion list an account
could silently replace is a list nobody can rely on; a house steering paragraph concatenated onto
a client's is how "we never bid on competitor names" and "this client sells competitor parts"
become one contradictory instruction. So the resolved policy hands a model two labelled strings
and lets it hold both.

## What is enforced

``protected_terms``, ``banned_phrases``, ``max_daily_budget``, ``max_budget_increase`` and
``max_cpc`` are checked before a mutation leaves the process. The rest shapes what an agent
proposes and is advice.

The protected-terms check is the one worth reading. A naive version refuses any proposed negative
that *contains* a protected word, and it is wrong in the direction that matters: an EXACT negative
on ``"beugel kosten"`` does not stop ``"beugel"`` from serving, so refusing it teaches an agency
that the guard cries wolf, and the next thing they do is switch it off. So the check models what
Google actually does — whether the proposed negative would **match** the protected term under the
proposed negative's own match type — and refuses only then.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

#: A list column is read on every write and reaches a model in every payload. Unbounded, it is
#: both a slow path and a way to make one tenant's tool call enormous. Over the cap is **cut and
#: reported**, never silently dropped — the caller is told which list was trimmed.
MAX_TERMS = 500

#: Google's own ceiling on one keyword, and therefore on one protected term.
MAX_TERM_LENGTH = 80

_WHITESPACE = re.compile(r"\s+")


def normalise(text: Any) -> str:
    """The form two spellings of one term have to share before they can be compared.

    Casefold rather than ``lower()``: Google matches keywords case-insensitively across scripts,
    and ``lower()`` gets the German ``ß`` wrong in exactly the market this product ships to.
    """
    return _WHITESPACE.sub(" ", str(text or "").strip()).casefold()


def _tokens(text: str) -> list[str]:
    return [part for part in normalise(text).split(" ") if part]


@dataclass(frozen=True)
class AdsPolicy:
    """The effective policy for one account: three layers already folded into one answer."""

    protected_terms: tuple[str, ...] = ()
    banned_phrases: tuple[str, ...] = ()
    always_exclude: tuple[str, ...] = ()
    max_daily_budget: float | None = None
    #: A fraction: ``1.0`` means one change may at most double a daily budget.
    max_budget_increase: float | None = None
    max_cpc: float | None = None
    waste_min_cost: float | None = None
    waste_min_clicks: int | None = None
    #: Kept apart on purpose — see the module docstring.
    house_steering: str = ""
    account_steering: str = ""
    house_ad_copy_rules: str = ""
    account_ad_copy_rules: str = ""
    #: i18n keys for anything the resolution itself had to cut.
    warnings: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """The shape a tool hands a model. Prose stays in two named fields."""
        return {
            "protected_terms": list(self.protected_terms),
            "always_exclude": list(self.always_exclude),
            "banned_phrases": list(self.banned_phrases),
            "max_daily_budget": self.max_daily_budget,
            "max_budget_increase": self.max_budget_increase,
            "max_cpc": self.max_cpc,
            "waste_min_cost": self.waste_min_cost,
            "waste_min_clicks": self.waste_min_clicks,
            "agency_steering": self.house_steering or None,
            "account_steering": self.account_steering or None,
            "agency_ad_copy_rules": self.house_ad_copy_rules or None,
            "account_ad_copy_rules": self.account_ad_copy_rules or None,
        }


#: The layer that is code. One substantive value, for the reason in the module docstring.
BUILT_IN = AdsPolicy(max_budget_increase=1.0)


def _terms(*sources: Any) -> tuple[tuple[str, ...], bool]:
    """Every source's terms, normalised, deduplicated, order-preserving, capped.

    Order-preserving because the house list is passed first and an agency reading its own policy
    back expects to see its own entries where it put them.
    """
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for raw in source or ():
            value = normalise(raw)[:MAX_TERM_LENGTH]
            if value and value not in seen:
                seen.add(value)
                out.append(value)
    return tuple(out[:MAX_TERMS]), len(out) > MAX_TERMS


def _scalar(*candidates: Any) -> float | None:
    """The first layer that expressed an opinion. ``None`` at every layer means no limit."""
    for value in candidates:
        if value is not None:
            return float(value)
    return None


def resolve(account_row: Any, house_row: Any) -> AdsPolicy:
    """Fold built-in, house and account into the one policy every check reads.

    Both rows are optional: an org that has never opened the screen resolves to :data:`BUILT_IN`
    plus empty lists, which is the posture an install upgrades into — nothing is refused that was
    not refused yesterday, except a budget change larger than the built-in relative ceiling, which
    is stated in the release notes and is the only behavioural change this brings to an existing
    account.
    """
    warnings: list[str] = []

    protected, cut = _terms(
        getattr(house_row, "protected_terms", ()), getattr(account_row, "protected_terms", ())
    )
    if cut:
        warnings.append("google_ads.warning.policy_terms_capped")
    banned, cut = _terms(
        getattr(house_row, "banned_phrases", ()), getattr(account_row, "banned_phrases", ())
    )
    if cut:
        warnings.append("google_ads.warning.policy_terms_capped")
    exclude, cut = _terms(
        getattr(house_row, "always_exclude", ()), getattr(account_row, "always_exclude", ())
    )
    if cut:
        warnings.append("google_ads.warning.policy_terms_capped")

    clicks = _scalar(
        getattr(account_row, "waste_min_clicks", None), getattr(house_row, "waste_min_clicks", None)
    )
    resolved = AdsPolicy(
        protected_terms=protected,
        banned_phrases=banned,
        always_exclude=exclude,
        max_daily_budget=_scalar(
            getattr(account_row, "max_daily_budget", None),
            getattr(house_row, "max_daily_budget", None),
            BUILT_IN.max_daily_budget,
        ),
        max_budget_increase=_scalar(
            getattr(account_row, "max_budget_increase_pct", None),
            getattr(house_row, "max_budget_increase_pct", None),
            BUILT_IN.max_budget_increase,
        ),
        max_cpc=_scalar(
            getattr(account_row, "max_cpc", None),
            getattr(house_row, "max_cpc", None),
            BUILT_IN.max_cpc,
        ),
        waste_min_cost=_scalar(
            getattr(account_row, "waste_min_cost", None),
            getattr(house_row, "waste_min_cost", None),
        ),
        waste_min_clicks=int(clicks) if clicks is not None else None,
        house_steering=str(getattr(house_row, "steering", "") or ""),
        account_steering=str(getattr(account_row, "steering", "") or ""),
        house_ad_copy_rules=str(getattr(house_row, "ad_copy_rules", "") or ""),
        account_ad_copy_rules=str(getattr(account_row, "ad_copy_rules", "") or ""),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    # The clamps run **last**, over whatever the layers produced, so a stored nonsense value
    # cannot become an enforcement rule. A negative ceiling would refuse every write and read as
    # the integration being broken.
    return replace(
        resolved,
        max_daily_budget=_non_negative(resolved.max_daily_budget),
        max_budget_increase=_non_negative(resolved.max_budget_increase),
        max_cpc=_non_negative(resolved.max_cpc),
        waste_min_cost=_non_negative(resolved.waste_min_cost),
        waste_min_clicks=(
            max(0, resolved.waste_min_clicks) if resolved.waste_min_clicks is not None else None
        ),
    )


def _non_negative(value: float | None) -> float | None:
    return None if value is None else max(0.0, value)


# --- the checks -------------------------------------------------------------------------------- #


def blocks(negative: str, match_type: str | None, protected: str) -> bool:
    """Whether a negative keyword would actually stop ``protected`` from serving.

    Google's own matching, in three lines, because approximating it is what makes a guard either
    useless or untrusted:

    * **EXACT** blocks only the identical term.
    * **PHRASE** blocks a term containing the negative's words *in order and adjacent*.
    * **BROAD** blocks a term containing all of the negative's words, in any order.

    An unknown or absent match type is treated as BROAD — the widest — because the failure
    direction here must be "refused something harmless" rather than "allowed something that
    silently stops the client's brand from serving".
    """
    left = _tokens(negative)
    right = _tokens(protected)
    if not left or not right:
        return False
    kind = (match_type or "BROAD").strip().upper()
    if kind == "EXACT":
        return left == right
    if kind == "PHRASE":
        return any(
            right[i : i + len(left)] == left for i in range(0, len(right) - len(left) + 1)
        )
    return set(left) <= set(right)


def protected_hit(policy: AdsPolicy, negative: str, match_type: str | None = None) -> str | None:
    """The protected term this exclusion would block, or ``None``.

    Returns the term rather than a boolean so the refusal can name it. "This exclusion is
    refused" is an instruction to argue with the software; "this exclusion would also block
    *beugel*, which is protected" is an instruction to fix the exclusion.
    """
    for term in policy.protected_terms:
        if blocks(negative, match_type, term):
            return term
    return None


def banned_hit(policy: AdsPolicy, *texts: str) -> str | None:
    """The banned phrase that appears in any of ``texts``, or ``None``.

    Substring on the normalised text, so casing and doubled spaces cannot smuggle one through.
    Checked *after* the copy is assembled rather than asked of a model beforehand — a phrase a
    tenant has banned is not a preference to be weighed (#300).
    """
    haystack = " ".join(normalise(text) for text in texts if text)
    if not haystack:
        return None
    for phrase in policy.banned_phrases:
        if phrase and phrase in haystack:
            return phrase
    return None


#: What a refused write reports, as ``(i18n key, the field it is about, the limit)``.
@dataclass(frozen=True)
class PolicyRefusal:
    key: str
    field: str
    limit: float | None = None
    subject: str | None = None


def budget_refusal(
    policy: AdsPolicy, *, amount: float, previous: float | None
) -> PolicyRefusal | None:
    """Whether a daily-budget write is outside the policy.

    Two independent limits, and the second one only exists for an *update*: a create has no
    previous value, so nothing relative can bound it and the absolute ceiling is the only guard
    there is. Said out loud because it is the gap somebody will otherwise find the hard way — an
    account with no ``max_daily_budget`` bounds a budget *create* by the permission alone.

    A decrease is never refused. It cannot spend money, and an agency reacting to an overspend at
    five on a Friday must not be arguing with us.
    """
    if amount < 0:
        return PolicyRefusal("errors.google_ads_budget_negative", "amount")
    if policy.max_daily_budget is not None and amount > policy.max_daily_budget:
        return PolicyRefusal(
            "errors.google_ads_budget_over_ceiling", "amount", policy.max_daily_budget
        )
    if (
        policy.max_budget_increase is not None
        and previous is not None
        and previous > 0
        and amount > previous * (1.0 + policy.max_budget_increase)
    ):
        return PolicyRefusal(
            "errors.google_ads_budget_increase_too_large",
            "amount",
            round(previous * (1.0 + policy.max_budget_increase), 2),
        )
    return None


def cpc_refusal(policy: AdsPolicy, *, amount: float | None) -> PolicyRefusal | None:
    """Whether a bid is above the policy's ceiling."""
    if amount is None:
        return None
    if amount < 0:
        return PolicyRefusal("errors.google_ads_bid_negative", "cpc_bid")
    if policy.max_cpc is not None and amount > policy.max_cpc:
        return PolicyRefusal("errors.google_ads_bid_over_ceiling", "cpc_bid", policy.max_cpc)
    return None

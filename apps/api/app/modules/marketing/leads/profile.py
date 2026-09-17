"""The measurement profile (*meetprofiel*): one client's vocabulary for the leads dashboard.

Everything a Looker report used to hardcode per client lives here as data, validated on write:

- **Roles** map a functional meaning (a request, a form start, a phone click) to the GA4 events
  that carry it for *this* client. A role holds several matchers because one client has three
  phone-click events, and a matcher may be a prefix or a regex because the events were named
  ``click_telefoon_430`` and ``click_telefoon_es_065``.
- **Dimensions** name the GA4 fields the dashboard may group by — the client's custom event
  parameters, registered in GA4 as custom dimensions — with a label and per-value labels, so
  ``internationaal-transport`` prints as *Internationaal transport* and nowhere in the code is
  either string.
- **Breakpoints** are the dates the measurement changed. A hard one is the floor of comparable
  reporting; either kind is marked on every time series and named in the fixed note.
- **Ads** says whether the advertising half is drawn and which conversion action means which
  service, since Google Ads receives no parameters and recognises the service by the action.

Absent means *not measured*, never *zero*: a role with no matchers withholds every widget that
needs it, and that is the honest answer for a client whose tracking does not carry it.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.errors import AppError

# --- functional roles ---------------------------------------------------------------------- #
ROLE_REQUEST = "request"
ROLE_FORM_STARTED = "form_started"
ROLE_FORM_SUBMITTED = "form_submitted"
ROLE_FORM_ERROR = "form_error"
ROLE_PHONE = "phone_click"
ROLE_EMAIL = "email_click"
ROLE_APPLICATION = "application"

ROLES: tuple[str, ...] = (
    ROLE_REQUEST,
    ROLE_FORM_STARTED,
    ROLE_FORM_SUBMITTED,
    ROLE_FORM_ERROR,
    ROLE_PHONE,
    ROLE_EMAIL,
    ROLE_APPLICATION,
)

# --- the dimensions a widget may group by --------------------------------------------------- #
DIM_SERVICE = "service"
DIM_FORM_TYPE = "form_type"
DIM_FORM_NAME = "form_name"
DIM_LANGUAGE = "language"
DIM_PAGE_PATH = "page_path"
DIM_PAGE_TITLE = "page_title"
DIM_ERROR_REASON = "error_reason"

DIMENSION_KEYS: tuple[str, ...] = (
    DIM_SERVICE,
    DIM_FORM_TYPE,
    DIM_FORM_NAME,
    DIM_LANGUAGE,
    DIM_PAGE_PATH,
    DIM_PAGE_TITLE,
    DIM_ERROR_REASON,
)

# --- channel grouping ----------------------------------------------------------------------- #
GROUP_ORGANIC = "organic"
GROUP_ADS = "ads"
GROUP_AI = "ai"
GROUP_OTHER = "other"
CHANNEL_GROUPS: tuple[str, ...] = (GROUP_ORGANIC, GROUP_ADS, GROUP_AI, GROUP_OTHER)

#: The house default. ``Cross-network`` is where Performance Max reports and it is
#: advertising; a grouping that files only Paid Search under ads misses most of the paid
#: traffic on any account running PMax. ``AI Assistant`` stays its own group on purpose: it is
#: the one channel a client asks about by name this year. Everything unlisted is *other*.
DEFAULT_CHANNEL_GROUPS: dict[str, list[str]] = {
    GROUP_ORGANIC: ["Organic Search", "Organic Social", "Organic Video", "Organic Shopping"],
    GROUP_ADS: [
        "Paid Search",
        "Paid Social",
        "Paid Video",
        "Paid Shopping",
        "Paid Other",
        "Cross-network",
        "Display",
    ],
    GROUP_AI: ["AI Assistant"],
}

MatchKind = Literal["exact", "begins_with", "contains", "regex"]
_LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_MAX_TEXT = 200
_MAX_VALUES = 200
_MAX_MATCHERS = 20


class EventMatcher(BaseModel):
    """One way of recognising an event name. ``exact`` is the common case; the others exist
    because a client's phone-click events differ only by suffix."""

    match: MatchKind = "exact"
    value: str = Field(min_length=1, max_length=100)

    @field_validator("value")
    @classmethod
    def _regex_compiles(cls, value: str, info) -> str:  # noqa: ANN001
        return value.strip()

    @model_validator(mode="after")
    def _regex_is_valid(self) -> EventMatcher:
        if self.match == "regex":
            try:
                re.compile(self.value)
            except re.error as exc:
                raise ValueError("invalid regex") from exc
        return self


class DimensionSpec(BaseModel):
    """A GA4 field the dashboard may group and filter by, and what it is called."""

    #: The GA4 Data API name — ``customEvent:dienst`` for a registered event parameter, or a
    #: standard dimension such as ``pagePath``. Never the parameter alone, so the profile says
    #: exactly what is sent to Google.
    field: str = Field(min_length=1, max_length=120)
    #: ``{locale: label}`` — tenant content, resolved per reader like every ``label_i18n``.
    label: dict[str, str] = Field(default_factory=dict)
    #: ``{raw value: {locale: label}}`` — what a value is called on screen. Absent = the raw
    #: value, which is also what a value the profile has never seen prints as.
    values: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: Whether the dashboard offers this dimension as a filter control.
    filterable: bool = True

    @field_validator("field")
    @classmethod
    def _field_shape(cls, value: str) -> str:
        value = value.strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_:]*$", value):
            raise ValueError("invalid GA4 dimension name")
        return value

    @field_validator("label")
    @classmethod
    def _label_locales(cls, value: dict[str, str]) -> dict[str, str]:
        return _clean_i18n(value)

    @field_validator("values")
    @classmethod
    def _values_shape(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        if len(value) > _MAX_VALUES:
            raise ValueError("too many values")
        return {
            raw[:_MAX_TEXT]: _clean_i18n(labels) for raw, labels in value.items() if raw.strip()
        }

    def value_label(self, raw: str, locale: str) -> str:
        labels = self.values.get(raw) or {}
        return labels.get(locale) or labels.get("nl") or labels.get("en") or raw

    def title(self, locale: str) -> str | None:
        return self.label.get(locale) or self.label.get("nl") or self.label.get("en") or None


class Breakpoint(BaseModel):
    """A date the measurement changed. ``hard``: figures before it are not comparable at all
    (a rebuilt tracking setup); ``soft``: worth a mark on the chart, not a floor."""

    date: date
    description: dict[str, str] = Field(default_factory=dict)
    severity: Literal["hard", "soft"] = "hard"

    @field_validator("description")
    @classmethod
    def _description_locales(cls, value: dict[str, str]) -> dict[str, str]:
        return _clean_i18n(value)

    def text(self, locale: str) -> str | None:
        return (
            self.description.get(locale)
            or self.description.get("nl")
            or self.description.get("en")
            or None
        )


class AdsSettings(BaseModel):
    """The advertising half: drawn for a client with a linked Ads account unless switched off,
    and the one place a conversion action is tied to a service."""

    enabled: bool = True
    #: ``{conversion action name: service value}`` — the raw value of the *service* dimension,
    #: so the Ads table prints the same label the GA4 widgets do for the same service.
    action_services: dict[str, str] = Field(default_factory=dict)

    @field_validator("action_services")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > _MAX_VALUES:
            raise ValueError("too many actions")
        return {
            k.strip()[:_MAX_TEXT]: v.strip()[:_MAX_TEXT]
            for k, v in value.items()
            if k.strip() and v.strip()
        }


class LeadProfile(BaseModel):
    """The whole profile, as stored on ``marketing_company_settings.lead_profile``."""

    #: Which links feed it. ``None`` = the client's first active link of that source, which is
    #: every client with one website; a client with two properties names the one that
    #: carries the forms.
    ga4_link_id: uuid.UUID | None = None
    gads_link_id: uuid.UUID | None = None
    roles: dict[str, list[EventMatcher]] = Field(default_factory=dict)
    dimensions: dict[str, DimensionSpec] = Field(default_factory=dict)
    #: The raw ``form_type`` values that count as a quote request — "waarvan offertes".
    quote_values: list[str] = Field(default_factory=list)
    ads: AdsSettings = Field(default_factory=AdsSettings)
    breakpoints: list[Breakpoint] = Field(default_factory=list)
    #: ``{group: [channel, …]}`` overriding the org's house grouping; ``None`` = inherit.
    channel_groups: dict[str, list[str]] | None = None
    #: Widget keys this client does not get. A profile decides what *can* exist; this is what
    #: the agency chose not to show — the layout idiom one level down.
    hidden_widgets: list[str] = Field(default_factory=list)
    #: A free sentence the tenant adds to the fixed note, per locale.
    disclaimer: dict[str, str] = Field(default_factory=dict)

    @field_validator("roles")
    @classmethod
    def _roles_known(cls, value: dict[str, list[EventMatcher]]) -> dict[str, list[EventMatcher]]:
        unknown = sorted(set(value) - set(ROLES))
        if unknown:
            raise ValueError(f"unknown role: {', '.join(unknown)}")
        for role, matchers in value.items():
            if len(matchers) > _MAX_MATCHERS:
                raise ValueError(f"too many matchers for {role}")
        # A role with no matchers is the same fact as a role never mentioned.
        return {role: matchers for role, matchers in value.items() if matchers}

    @field_validator("dimensions")
    @classmethod
    def _dimensions_known(cls, value: dict[str, DimensionSpec]) -> dict[str, DimensionSpec]:
        unknown = sorted(set(value) - set(DIMENSION_KEYS))
        if unknown:
            raise ValueError(f"unknown dimension: {', '.join(unknown)}")
        return value

    @field_validator("quote_values")
    @classmethod
    def _quote_values(cls, value: list[str]) -> list[str]:
        return [v.strip()[:_MAX_TEXT] for v in value if v.strip()][:_MAX_VALUES]

    @field_validator("channel_groups")
    @classmethod
    def _channel_groups(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if value is None:
            return None
        return validate_channel_groups(value)

    @field_validator("hidden_widgets")
    @classmethod
    def _hidden_widgets(cls, value: list[str]) -> list[str]:
        from app.modules.marketing.leads.widgets import WIDGET_KEYS  # noqa: PLC0415 — cycle

        unknown = sorted(set(value) - set(WIDGET_KEYS))
        if unknown:
            raise ValueError(f"unknown widget: {', '.join(unknown)}")
        return sorted(set(value))

    @field_validator("disclaimer")
    @classmethod
    def _disclaimer(cls, value: dict[str, str]) -> dict[str, str]:
        return _clean_i18n(value, max_len=1000)

    @field_validator("breakpoints")
    @classmethod
    def _breakpoints_sorted(cls, value: list[Breakpoint]) -> list[Breakpoint]:
        if len(value) > 50:
            raise ValueError("too many breakpoints")
        return sorted(value, key=lambda b: b.date)

    # --- reads ---------------------------------------------------------------------------- #
    def has_role(self, role: str) -> bool:
        return bool(self.roles.get(role))

    def has_dimension(self, key: str) -> bool:
        return key in self.dimensions

    def dimension(self, key: str) -> DimensionSpec | None:
        return self.dimensions.get(key)

    def hard_breakpoint(self) -> Breakpoint | None:
        """The latest hard breakpoint — the floor of comparable reporting."""
        hard = [b for b in self.breakpoints if b.severity == "hard"]
        return hard[-1] if hard else None

    def disclaimer_text(self, locale: str) -> str | None:
        return (
            self.disclaimer.get(locale)
            or self.disclaimer.get("nl")
            or self.disclaimer.get("en")
            or None
        )


def _clean_i18n(value: dict[str, str], *, max_len: int = _MAX_TEXT) -> dict[str, str]:
    out: dict[str, str] = {}
    for locale, text in value.items():
        if not _LOCALE_RE.match(locale):
            raise ValueError(f"invalid locale: {locale}")
        cleaned = (text or "").strip()
        if cleaned:
            out[locale] = cleaned[:max_len]
    return out


def validate_channel_groups(value: dict[str, list[str]]) -> dict[str, list[str]]:
    """The org-level (or overriding) channel grouping: known groups, bounded, deduplicated."""
    unknown = sorted(set(value) - set(CHANNEL_GROUPS))
    if unknown:
        raise ValueError(f"unknown channel group: {', '.join(unknown)}")
    out: dict[str, list[str]] = {}
    seen: set[str] = set()
    for group in CHANNEL_GROUPS:
        channels: list[str] = []
        for channel in value.get(group) or []:
            name = channel.strip()[:80]
            if not name or name in seen:
                continue
            seen.add(name)
            channels.append(name)
        if channels:
            out[group] = channels
    return out


def parse_profile(stored: dict | None) -> LeadProfile | None:
    """The stored JSON as a profile, or ``None`` — a stored shape a later release no longer
    accepts is treated as absent rather than 500-ing a dashboard (the ``resolve_compare``
    rule): the editor shows it as unconfigured and the next save rewrites it."""
    if not stored:
        return None
    try:
        return LeadProfile.model_validate(stored)
    except ValueError:
        return None


def validate_profile(payload: dict) -> LeadProfile:
    """Refuse an invalid profile with the field named, the way every other settings write does."""
    try:
        return LeadProfile.model_validate(payload)
    except ValueError as exc:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"lead_profile": "errors.marketing_lead_profile_invalid"},
            details={"reason": str(exc)[:500]},
        ) from exc


def resolve_channel_groups(
    org_groups: dict | None, profile: LeadProfile | None
) -> dict[str, list[str]]:
    """The grouping a client's channel widgets use: the client's own, else the agency's, else
    the code default. Each is a whole mapping, never a diff — a grouping is small enough to
    state in full, and a diff over lists is a merge nobody can predict."""
    if profile is not None and profile.channel_groups:
        return profile.channel_groups
    if org_groups:
        try:
            parsed = validate_channel_groups(org_groups)
        except ValueError:
            parsed = {}
        if parsed:
            return parsed
    return {group: list(channels) for group, channels in DEFAULT_CHANNEL_GROUPS.items()}


def channel_group_for(channel: str, groups: dict[str, list[str]]) -> str:
    """Which group a GA4 channel name falls in; anything unlisted is *other*."""
    for group, channels in groups.items():
        if channel in channels:
            return group
    return GROUP_OTHER

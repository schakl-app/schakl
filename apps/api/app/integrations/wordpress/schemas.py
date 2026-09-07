"""Pydantic schemas for the wordpress module (docs/WORDPRESS.md).

Two conventions, both borrowed from ``cloudflare`` because they were right there:

* **The application password is write-only.** It goes in on create/update and never comes back
  out — not in a read model, not in the OpenAPI spec, not masked. ``password_configured`` is
  the only thing a client learns about it.
* **A refusal names its problem as a key, not a sentence.** ``last_error`` and the
  ``capability_errors`` *slugs* are stable machine strings the web resolves to
  ``wordpress.issue.*`` messages, so the API never picks a locale for someone else's screen
  (CLAUDE.md §8). The site's own text rides alongside as evidence, untranslated on purpose:
  it is a quote, and translating a quote is how a diagnosis stops matching the log line an
  admin is looking at.

Names are prefixed (``WordPressSiteRead``, not ``SiteRead``): a generic Pydantic name makes
FastAPI qualify *both* colliding modules' components in the generated client.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.wordpress.client import normalise_base_url


class WordPressSiteRead(BaseModel):
    """A connected WordPress. Never carries the application password."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    website_id: uuid.UUID
    base_url: str
    username: str
    active: bool
    status: str
    #: A stable key (``wordpress.issue.*``), never a sentence.
    last_error: str | None = None

    #: Observed at probe time — the keys of ``client.CAPABILITIES``. A **missing** key means
    #: "not probed", which is a different screen from ``False`` ("probed and refused").
    capabilities: dict[str, bool] = Field(default_factory=dict)
    #: Why a probe answered no, keyed the same way. Only ever holds keys whose capability is
    #: ``False`` — a ✗ with no explanation is the one state an admin cannot act on.
    capability_errors: dict[str, str] = Field(default_factory=dict)
    #: NULL means nobody has ever looked, which an empty ``capabilities`` cannot say on its own.
    capabilities_checked_at: datetime | None = None

    mcp_server_path: str | None = None
    #: Whose site this is, resolved through website → domain → client, so an agent listing
    #: forty sites can pick a client's without a second call per row.
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    domain_name: str | None = None
    rankmath_version: str | None = None
    #: Whether this Rank Math is new enough to have AI Visibility at all (≥ 1.0.273). Resolved
    #: server-side so the panel never re-implements a version comparison in two languages.
    rankmath_ai_visibility: bool = False

    last_verified_at: datetime | None = None
    #: Whether a password is stored at all. The password itself never leaves the server.
    password_configured: bool = True
    created_at: datetime
    updated_at: datetime


class WordPressSiteCreate(BaseModel):
    website_id: uuid.UUID
    #: Absolute site URL, subpath preserved. Normalised on the way in so ``https://klant.nl/``
    #: and ``https://klant.nl`` cannot become two credentials for one site.
    base_url: str = Field(min_length=1, max_length=500)
    username: str = Field(min_length=1, max_length=255)
    #: A WordPress **Application Password** (Gebruikers → Profiel → Toepassingswachtwoorden),
    #: never the account password. WordPress shows it space-separated in groups of four; both
    #: forms authenticate, so it is accepted as displayed.
    app_password: str = Field(min_length=8, max_length=512)
    active: bool = True

    @field_validator("base_url")
    @classmethod
    def _normalise(cls, value: str) -> str:
        normalised = normalise_base_url(value)
        if not normalised:
            raise ValueError("errors.invalid_url")
        return normalised


class WordPressSiteUpdate(BaseModel):
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    #: Omit to keep the stored password; send a new one to rotate. Never send an empty string
    #: to clear it — a connected site without a credential is not a state this module has a use
    #: for, and "disconnect" is a DELETE.
    app_password: str | None = Field(default=None, min_length=8, max_length=512)
    active: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _normalise(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = normalise_base_url(value)
        if not normalised:
            raise ValueError("errors.invalid_url")
        return normalised


class WordPressVerifyResult(BaseModel):
    """What a verify learned.

    ``ok`` is **not** "every capability is true" — a site with no Rank Math is a perfectly good
    WordPress connection, and a probe that reported it as broken would be the health-check
    mistake this module exists to avoid. ``ok`` is "at least one probe got through", i.e. the
    credential is real; the capability map is where the nuance lives.
    """

    ok: bool
    status: str
    capabilities: dict[str, bool] = Field(default_factory=dict)
    capability_errors: dict[str, str] = Field(default_factory=dict)
    rankmath_version: str | None = None
    rankmath_ai_visibility: bool = False
    mcp_server_path: str | None = None
    #: How many Rank Math brands this site tracks, where AI Visibility answered. ``None`` where
    #: it did not — zero brands and no Rank Math are different sentences.
    brand_count: int | None = None
    #: A stable ``wordpress.issue.*`` key where the whole credential was refused.
    error: str | None = None


class WordPressBrand(BaseModel):
    """One tracked brand, as the marketing picker and the panel need it.

    A hand-written subset of Rank Math's row rather than a passthrough: the upstream shape is a
    third party's and carries fields (``created_at``, cache bookkeeping) that would become our
    contract the moment they appeared in the spec.
    """

    id: str
    name: str
    url: str = ""
    locale: str | None = None
    status: str = "active"
    score: float | None = None
    rank: float | None = None
    avg_sentiment: float | None = None
    mentions: float | None = None
    citations: float | None = None
    analysis_status: str | None = None
    last_analyzed: str | None = None


def brand_from_payload(row: Any) -> WordPressBrand | None:
    """One Rank Math overview row → :class:`WordPressBrand`, defensively.

    Every field is optional in practice: ``/overview`` omits ``description`` by design, and a
    brand mid-analysis carries nulls where numbers will be. A row with no id is unusable and is
    dropped rather than guessed at.
    """
    if not isinstance(row, dict):
        return None
    brand_id = row.get("id")
    if not isinstance(brand_id, str) or not brand_id:
        return None

    def num(key: str) -> float | None:
        value = row.get(key)
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def txt(key: str) -> str | None:
        value = row.get(key)
        return value if isinstance(value, str) and value else None

    return WordPressBrand(
        id=brand_id,
        name=txt("name") or brand_id,
        url=txt("url") or "",
        locale=txt("locale"),
        status=txt("status") or "active",
        score=num("score"),
        rank=num("rank"),
        avg_sentiment=num("avg_sentiment"),
        mentions=num("mentions"),
        citations=num("citations"),
        analysis_status=txt("analysis_status"),
        last_analyzed=txt("last_analyzed"),
    )


# ---------------------------------------------------------------- the site as a surface
#
# Every shape below is **hand-written**, never a passthrough of WordPress's own response
# (`PublicInvoiceRead`'s rule): a subset expressed as an omission leaks the next field a
# plugin adds, and a client's `wp/v2` row carries author e-mail addresses, edit locks and
# `_links` an agent has no use for and a context budget cannot afford (docs/MCP.md).


class WordPressContentType(BaseModel):
    slug: str
    name: str
    rest_base: str
    hierarchical: bool = False


class WordPressSiteSummary(BaseModel):
    """What a connected site is: name, versions, the post types and plugins it carries."""

    site_id: uuid.UUID
    base_url: str
    name: str | None = None
    description: str | None = None
    #: From ``core/get-environment-info`` where the site has abilities; ``None`` where it does
    #: not, never guessed (the model's own "no ``wp_version`` column" rule).
    wp_version: str | None = None
    php_version: str | None = None
    #: The site's own locale and timezone, as WordPress states them.
    locale: str | None = None
    timezone: str | None = None
    #: Post types this credential may edit, with the REST base each answers on.
    content_types: list[WordPressContentType] = Field(default_factory=list)
    #: The REST namespaces the site registers — the honest plugin inventory: ``contact-form-7/v1``
    #: says CF7 is there, ``wpml/v1`` that pages come per language.
    namespaces: list[str] = Field(default_factory=list)
    has_forms: bool = False
    has_abilities: bool = False
    multilingual: bool = False


class WordPressContentRow(BaseModel):
    """One record in a list: enough to pick it, never its body."""

    id: int
    type: str
    slug: str
    title: str
    status: str
    link: str | None = None
    modified: str | None = None
    parent: int | None = None
    #: WPML's language, where the site says one. ``None`` on a monolingual site.
    lang: str | None = None


class WordPressContentList(BaseModel):
    items: list[WordPressContentRow]
    #: The site's own count for the filter — ``None`` where it did not say (§17).
    total: int | None = None
    page: int
    per_page: int


class WordPressContentRead(WordPressContentRow):
    """One record whole: raw content (block or classic HTML), the ACF fields, the meta."""

    #: The stored markup — block comments on a block-built page, classic HTML otherwise.
    content: str = ""
    #: What the theme renders it as, for a reader who wants the page rather than its source.
    rendered: str = ""
    excerpt: str = ""
    template: str | None = None
    #: ACF field values, exactly as ACF exposes them on this post type — present only where a
    #: field group has "Show in REST API" on. Images and relations arrive as ids; resolve them
    #: through the media read.
    acf: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    featured_media: int | None = None
    author: int | None = None
    date: str | None = None


#: WordPress statuses a visitor (or a logged-in reader) can see. Setting one, or editing a
#: record that carries one, is `wordpress.content.publish` rather than `.write`.
LIVE_STATUSES = frozenset({"publish", "future", "private"})
WRITABLE_STATUSES = frozenset({"publish", "future", "draft", "pending", "private"})


class WordPressContentWrite(BaseModel):
    """What an update may change. Absent means leave alone (§18); nothing here is clearable
    to ``null`` because WordPress has no empty title or content to clear to."""

    title: str | None = Field(default=None, max_length=500)
    #: Raw content, in the site's own storage format. For a block-built page that is block
    #: markup; sending plain HTML to one turns it classic, which is a real edit and a visible
    #: one — read the record first.
    content: str | None = None
    excerpt: str | None = None
    slug: str | None = Field(default=None, max_length=200)
    status: str | None = None
    template: str | None = None
    parent: int | None = None
    #: ACF field values to set. Merged by ACF field by field; a field left out is untouched,
    #: ``null`` clears one.
    acf: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    featured_media: int | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str | None) -> str | None:
        if value is not None and value not in WRITABLE_STATUSES:
            raise ValueError("errors.wordpress_unknown_status")
        return value


class WordPressContentCreate(WordPressContentWrite):
    """A new record. ``draft`` unless told otherwise, because a create that publishes by
    default is a create nobody reviews."""

    type: str = "page"
    title: str = Field(min_length=1, max_length=500)
    status: str = "draft"
    #: WPML: which language the record is created in.
    lang: str | None = Field(default=None, max_length=10)


class WordPressMediaRow(BaseModel):
    id: int
    title: str
    source_url: str
    mime_type: str | None = None
    alt: str = ""
    width: int | None = None
    height: int | None = None
    date: str | None = None


class WordPressMediaList(BaseModel):
    items: list[WordPressMediaRow]
    total: int | None = None
    page: int
    per_page: int


class WordPressFormRow(BaseModel):
    id: int
    slug: str
    title: str
    locale: str | None = None


class WordPressFormMail(BaseModel):
    subject: str = ""
    sender: str = ""
    recipient: str = ""
    body: str = ""
    additional_headers: str = ""
    attachments: str = ""
    use_html: bool = False
    exclude_blank: bool = False
    active: bool = True


class WordPressFormRead(WordPressFormRow):
    """One form whole: its template, both mails, its messages and its extra settings."""

    #: The form template — CF7's own tag language (``[text* your-name]``).
    form: str = ""
    #: The input names the template declares, read off CF7's own parse of it.
    fields: list[str] = Field(default_factory=list)
    mail: WordPressFormMail = Field(default_factory=WordPressFormMail)
    mail_2: WordPressFormMail = Field(default_factory=WordPressFormMail)
    messages: dict[str, str] = Field(default_factory=dict)
    additional_settings: str = ""
    #: What CF7's configuration validator complains about, keyed by property. Empty when
    #: it is happy or when the site does not run the validator.
    config_errors: dict[str, Any] = Field(default_factory=dict)


class WordPressFormWrite(BaseModel):
    """``wpcf7_save_contact_form``'s flat shape. Absent means leave alone."""

    title: str | None = Field(default=None, max_length=200)
    locale: str | None = Field(default=None, max_length=20)
    form: str | None = None
    mail: WordPressFormMail | None = None
    mail_2: WordPressFormMail | None = None
    messages: dict[str, str] | None = None
    additional_settings: str | None = None


class WordPressFormCreate(WordPressFormWrite):
    title: str = Field(min_length=1, max_length=200)
    form: str = Field(min_length=1)


class WordPressAbility(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    category: str | None = None
    #: The plugin's own claims: ``readonly``, ``destructive``, ``idempotent``.
    annotations: dict[str, bool] = Field(default_factory=dict)
    readonly: bool = False
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class WordPressAbilityRun(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    input: Any = None


class WordPressAbilityResult(BaseModel):
    name: str
    readonly: bool
    output: Any = None

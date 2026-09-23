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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    #: The schakl WordPress MCP Bridge plugin's version where the last probe found it;
    #: ``None`` where not.
    bridge_version: str | None = None

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
    bridge_version: str | None = None
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


#: The verbs a passthrough may send. ``HEAD``/``OPTIONS`` answer nothing an agent can use.
REST_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


class WordPressRestCall(BaseModel):
    """One call to the site's REST API, verbatim, under the stored credential.

    ``path`` is relative to ``/wp-json/`` (``wp/v2/settings``, ``wpml/v1/…``); a leading slash
    or a leading ``/wp-json/`` is tolerated because that is how people paste them.
    """

    method: str = "GET"
    path: str = Field(min_length=1, max_length=500)
    #: Query parameters. For a GET, the whole request.
    params: dict[str, Any] | None = None
    #: JSON body, for anything but a GET.
    body: Any = None

    @field_validator("method")
    @classmethod
    def _known_method(cls, value: str) -> str:
        method = value.strip().upper()
        if method not in REST_METHODS:
            raise ValueError("errors.wordpress_rest_method")
        return method


class WordPressRestResult(BaseModel):
    method: str
    #: The path as sent, normalised, so the caller sees what the deny-list judged.
    path: str
    data: Any = None
    #: ``X-WP-Total`` where the site sent one.
    total: int | None = None
    #: The answer was cut to fit the response ceiling. ``shown`` is how many rows survived
    #: of a list; ``dropped`` names the keys removed from an object. Never silent (§17).
    truncated: bool = False
    shown: int | None = None
    dropped: list[str] = Field(default_factory=list)


# ----------------------------------------------------------- the schakl WordPress MCP Bridge plugin
#
# The plugin is the contract and this file mirrors it (docs/WORDPRESS.md §9). Request bodies are
# typed because they become the MCP tools' input schemas — a model reads these descriptions to
# decide what to send. Responses are open models with the few fields every caller reads named,
# because the plugin's shape (a record's `fields`, its `references`, a schema's groups) is
# already documented once, in the plugin, and a second copy that could drift would be worse
# than none.


class _BridgeOpen(BaseModel):
    """A response the plugin shaped; extra keys pass through untouched."""

    model_config = ConfigDict(extra="allow")


class WordPressBridgeInfo(_BridgeOpen):
    """What the site is through the plugin: versions (WordPress, PHP, ACF, WPML), every post
    type including ones hidden from ``wp/v2``, taxonomies, ACF options pages, menus,
    languages, the SEO plugin, and what the stored credential's user may do."""

    site_id: uuid.UUID
    base_url: str
    bridge_version: str | None = None
    plugin: dict[str, Any] = Field(default_factory=dict)
    site: dict[str, Any] = Field(default_factory=dict)
    acf: dict[str, Any] | None = None
    wpml: dict[str, Any] | None = None
    seo: str | None = None
    post_types: list[dict[str, Any]] = Field(default_factory=list)
    taxonomies: list[dict[str, Any]] = Field(default_factory=list)
    options_pages: list[dict[str, Any]] = Field(default_factory=list)
    menus: list[dict[str, Any]] = Field(default_factory=list)
    user: dict[str, Any] = Field(default_factory=dict)


class WordPressBridgeSchema(_BridgeOpen):
    """The ACF field groups that apply to a post type, a record, an options page or a
    taxonomy: every field with name, type, label, required, choices, sub fields / layouts,
    the conditions that hide it, and the JSON shape the writer takes (``value_format``)."""

    subject: dict[str, Any] = Field(default_factory=dict)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    notes: dict[str, str] = Field(default_factory=dict)


class WordPressRecordRow(_BridgeOpen):
    id: int
    post_type: str
    title: str
    slug: str
    status: str
    link: str | None = None
    modified: str | None = None
    #: Parent chain as titles, root first — how a page list reads on a hierarchical type.
    path: list[str] = Field(default_factory=list)
    lang: str | None = None
    #: WPML: ``{lang: id}`` for the translation group.
    translations: dict[str, int] = Field(default_factory=dict)


class WordPressRecordList(_BridgeOpen):
    items: list[WordPressRecordRow] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 20
    pages: int = 0
    post_type: str


class WordPressRecord(_BridgeOpen):
    """One record whole. ``fields`` is the ACF tree in the requested mode — ``compact`` keeps,
    per page-builder row, only the fields the editor shows for that row and only the ones
    that hold something — and ``references`` resolves every attachment, post and term id the
    values name (url, alt, title, type)."""

    id: int
    post_type: str
    title: str
    status: str
    link: str | None = None
    content: str = ""
    fields: dict[str, Any] | None = None
    fields_mode: str | None = None
    references: dict[str, Any] = Field(default_factory=dict)
    taxonomies: dict[str, Any] = Field(default_factory=dict)
    seo: dict[str, Any] | None = None
    translations: dict[str, Any] | None = None
    schema_: dict[str, Any] | list[Any] | None = Field(default=None, alias="schema")


_FIELDS_DOC = (
    "ACF values keyed by field name; each named top-level field is replaced whole (a repeater "
    "by its full list of rows). Rows are objects of sub field names; flexible rows carry "
    "\"_layout\". Image/file/gallery values take an attachment id, a URL, or "
    "{\"upload\": {\"url\"|\"base64\", \"filename\", \"alt\", \"title\"}}. Read the schema first."
)
_OPS_DOC = (
    "Surgical edits applied to the stored values before validation, so one row changes "
    "without resending the rest: {\"op\": \"set\"|\"merge\"|\"append\"|\"insert\"|\"remove\"|"
    "\"move\", \"path\": \"blokken_blokken[2].titel\", \"value\"?, \"index\"?, \"from\"?, "
    "\"to\"?}."
)


class WordPressRecordCreate(BaseModel):
    """A new record of any post type, through the plugin: validated against the ACF schema
    before anything is written, so a refusal carries every problem with its path and leaves
    nothing behind."""

    post_type: str = Field(max_length=40, description="Post type slug (from the bridge info).")
    title: str = Field(max_length=500)
    status: str = Field(
        "draft", description="draft (default) | pending | publish | private | future."
    )
    content: str | None = Field(None, description="Main content (HTML).")
    excerpt: str | None = None
    slug: str | None = None
    parent: int | None = None
    template: str | None = Field(None, description="Page template file name.")
    menu_order: int | None = None
    date: str | None = Field(
        None, description="ISO 8601; with status future, when it goes live."
    )
    featured_media: int | str | dict[str, Any] | None = Field(
        None, description="Attachment id, URL, or {\"upload\": {...}}."
    )
    terms: dict[str, list[int | str]] | None = Field(
        None,
        description="Terms per taxonomy: {\"category\": [3, \"Nieuws\"]} — ids, names or slugs.",
    )
    seo: dict[str, Any] | None = Field(
        None,
        description=(
            "Rank Math / Yoast meta: {title, description, focus_keyword, canonical, noindex}."
        ),
    )
    meta: dict[str, Any] | None = Field(None, description="Plain post meta; null deletes a key.")
    fields: dict[str, Any] | None = Field(None, description=_FIELDS_DOC)
    lang: str | None = Field(None, description="WPML language code for the new record.")
    translation_of: int | None = Field(None, description="WPML: the id this record translates.")


class WordPressRecordUpdate(BaseModel):
    """Change a record through the plugin. Absent keys are left alone; ``fields`` replaces
    the named top-level ACF fields whole; ``ops`` edits inside the stored values by path."""

    title: str | None = Field(None, max_length=500)
    status: str | None = Field(None, description="draft | pending | publish | private | future.")
    content: str | None = None
    excerpt: str | None = None
    slug: str | None = None
    parent: int | None = None
    template: str | None = None
    menu_order: int | None = None
    date: str | None = None
    featured_media: int | str | dict[str, Any] | None = None
    terms: dict[str, list[int | str]] | None = None
    seo: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    fields: dict[str, Any] | None = Field(None, description=_FIELDS_DOC)
    ops: list[dict[str, Any]] | None = Field(None, description=_OPS_DOC)
    mode: str | None = Field(
        None, description="Read mode of the returned record: compact | visible | full | none."
    )


class WordPressBridgeDelete(_BridgeOpen):
    id: int
    trashed: bool = False
    deleted: bool = False


class WordPressMediaUpload(BaseModel):
    """Add a file to the site's media library from a public URL (fetched by the site) or a
    base64 body. A URL already in the site's own library is reused rather than copied."""

    url: str | None = Field(None, description="Public http(s) URL to fetch.")
    base64: str | None = Field(None, description="The file body, base64 (a data: URL is accepted).")
    filename: str | None = Field(None, description="Name with extension; derived when omitted.")
    mime: str | None = None
    title: str | None = None
    alt: str | None = Field(None, description="Alt text — always give an image one.")
    caption: str | None = None
    description: str | None = None
    attach_to: int | None = Field(None, description="Record id the file belongs to.")
    set_featured: bool = Field(False, description="Also make it that record's featured image.")
    lang: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> WordPressMediaUpload:
        if not self.url and not self.base64:
            raise ValueError("errors.wordpress_upload_source")
        return self


class WordPressBridgeMedia(_BridgeOpen):
    id: int
    url: str | None = None
    alt: str = ""
    title: str = ""
    mime: str | None = None
    width: int | None = None
    height: int | None = None


class WordPressBridgeTerm(_BridgeOpen):
    id: int
    taxonomy: str
    name: str
    slug: str
    parent: int | None = None
    lang: str | None = None
    fields: dict[str, Any] | None = None


class WordPressBridgeTermList(_BridgeOpen):
    items: list[WordPressBridgeTerm] = Field(default_factory=list)
    total: int = 0
    taxonomy: str


class WordPressBridgeTermCreate(BaseModel):
    taxonomy: str = Field(max_length=40)
    name: str = Field(max_length=200)
    slug: str | None = None
    parent: int | None = None
    description: str | None = None
    fields: dict[str, Any] | None = Field(None, description="ACF values keyed by field name.")
    lang: str | None = None
    translation_of: int | None = Field(
        None, description="WPML: the source term's term_taxonomy_id."
    )


class WordPressOptionsPages(_BridgeOpen):
    items: list[dict[str, Any]] = Field(default_factory=list)


class WordPressOptionsRead(_BridgeOpen):
    page: str
    title: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    references: dict[str, Any] = Field(default_factory=dict)


class WordPressOptionsWrite(BaseModel):
    """Write ACF values on an options page — live at once, site-wide."""

    fields: dict[str, Any] | None = Field(None, description=_FIELDS_DOC)
    ops: list[dict[str, Any]] | None = Field(None, description=_OPS_DOC)
    lang: str | None = None

    @model_validator(mode="after")
    def _something(self) -> WordPressOptionsWrite:
        if not self.fields and not self.ops:
            raise ValueError("errors.nothing_to_update")
        return self


class WordPressMenuList(_BridgeOpen):
    items: list[dict[str, Any]] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class WordPressMenu(_BridgeOpen):
    id: int
    name: str
    slug: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class WordPressMenuItemAdd(BaseModel):
    """Add a record, a term or a custom link to a menu. Live at once."""

    object_id: int | None = Field(None, description="A record id to link.")
    term_id: int | None = None
    taxonomy: str | None = None
    url: str | None = Field(None, description="A custom link (needs a title).")
    title: str | None = None
    parent: int | None = Field(None, description="Parent menu item id.")
    position: int | None = None
    target: bool = False


class WordPressLanguages(_BridgeOpen):
    default_language: str | None = None
    languages: list[dict[str, Any]] = Field(default_factory=list)
    current: str | None = None


class WordPressTranslations(_BridgeOpen):
    id: int
    post_type: str
    lang: str | None = None
    translations: dict[str, Any] = Field(default_factory=dict)
    languages: list[str] = Field(default_factory=list)


class WordPressTranslationCreate(BaseModel):
    """WPML: create a record's translation in another language, linked to it. The source is
    copied first (core fields, terms, featured image, ACF fields with every referenced id
    swapped for its translation where one exists), then what you send is applied on top —
    so send the translated title, content and fields and nothing else. Pass
    ``translation_id`` instead to link an *existing* record as the translation."""

    lang: str = Field(max_length=10, description="Target language code.")
    translation_id: int | None = Field(
        None, description="Connect this existing record as the translation instead of creating one."
    )
    title: str | None = None
    content: str | None = None
    excerpt: str | None = None
    slug: str | None = None
    model_config = ConfigDict(populate_by_name=True)

    status: str | None = Field(
        None, description="draft (default) | pending | publish | private."
    )
    #: Named `copy_source` because `copy` is a BaseModel method; the plugin reads `copy`.
    copy_source: str | None = Field(
        None, alias="copy", description="all (default) | none."
    )
    overwrite: bool = Field(
        False, description="Update an existing translation instead of refusing."
    )
    seo: dict[str, Any] | None = None
    fields: dict[str, Any] | None = Field(None, description=_FIELDS_DOC)
    ops: list[dict[str, Any]] | None = Field(None, description=_OPS_DOC)


class WordPressStringList(_BridgeOpen):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class WordPressStringUpdate(BaseModel):
    """WPML String Translation: set a string's translation in a language."""

    id: int | None = Field(None, description="String id (from the list).")
    domain: str | None = None
    name: str | None = None
    lang: str = Field(max_length=10)
    value: str


class WordPressStringResult(_BridgeOpen):
    id: int
    lang: str
    value: str
    updated: bool = True

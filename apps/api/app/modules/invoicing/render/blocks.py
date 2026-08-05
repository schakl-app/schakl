"""What a document is made of, and what a tenant may rearrange (issue #207 follow-up).

A template no longer hardcodes which pieces print. It carries a **layout**: an ordered list
of blocks, each toggleable, each with its own ordered list of toggleable fields. This module
is the catalog those keys are drawn from — `role_permissions`' rule applied to design (§15,
"registry, not free text"): a stored layout only ever names a key this file defines, so a
config written by an older release still resolves, and a typo cannot invent a field.

Three properties earn their complexity:

* **A stored layout is a diff, not a snapshot.** Resolution starts from the catalog and lets
  the layout reorder and toggle what it mentions. A block or field the layout has never heard
  of — because it shipped in a later release — appears at its catalog position with its
  catalog default. Otherwise every new field would be invisible to every existing tenant, and
  the first person to notice would be a customer reading an invoice missing its VAT number.
* **Regions belong to the design, not the config.** A block's *place* (the header's left
  column, the body stack) is a property of the design that draws it; only the body stack is
  genuinely an ordered list, so only that order is offered. Letting a config move `seller`
  into the totals card would produce configurations no design can honour.
* **Legality is not a preference.** `LOCKED_FIELDS` may be reordered but never switched off:
  an invoice that omits its own number, its date or its VAT breakdown is not a document a
  tenant is allowed to send, whatever the toggle said.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Region = Literal["identity", "sender", "addressee", "meta", "body"]


@dataclass(frozen=True)
class FieldSpec:
    """One printable field inside a block. ``key`` is stored; the label is looked up."""

    key: str
    #: Off by default — present in the catalog so a tenant can switch it on, not printed
    #: until they do. Everything else defaults on.
    default: bool = True
    #: Legally or structurally required: reorderable, never removable.
    locked: bool = False
    #: Whether the field prints a label at all, and so whether a tenant may reword it. An
    #: address line prints as a line, not as "Adres: …"; an override there would not rename
    #: anything, it would *introduce* a label — and in the letterhead move the name out of
    #: the address stack and into the labelled grid. Off here means the override is dropped.
    labelled: bool = True


@dataclass(frozen=True)
class BlockSpec:
    key: str
    region: Region
    default: bool = True
    locked: bool = False
    fields: tuple[FieldSpec, ...] = ()
    #: Body blocks are the only ones a tenant may reorder — see the module docstring.
    @property
    def movable(self) -> bool:
        return self.region == "body"


def _f(
    key: str, *, default: bool = True, locked: bool = False, labelled: bool = True
) -> FieldSpec:
    return FieldSpec(key=key, default=default, locked=locked, labelled=labelled)


#: A party's own lines print as an address, not as labelled rows — see ``FieldSpec.labelled``.
def _line(key: str, *, default: bool = True, locked: bool = False) -> FieldSpec:
    return _f(key, default=default, locked=locked, labelled=False)


#: The catalog, in the order a fresh template starts out in. Body blocks print top to bottom.
BLOCK_CATALOG: tuple[BlockSpec, ...] = (
    BlockSpec("logo", "identity"),
    BlockSpec("heading", "identity", locked=True),
    BlockSpec(
        "seller",
        "sender",
        fields=(
            _line("name", locked=True),
            _line("address"),
            _line("postal_city"),
            _line("country", default=False),
            _f("phone"),
            _f("email"),
            _f("website", default=False),
            _f("iban"),
            _f("bic", default=False),
            _f("vat_number", locked=True),
            _f("coc_number"),
        ),
    ),
    BlockSpec(
        "bill_to",
        "addressee",
        locked=True,
        fields=(
            _line("label"),
            _line("name", locked=True),
            _line("attn"),
            _line("address"),
            _line("postal_city"),
            _line("country"),
            _f("vat_number"),
            _f("coc_number"),
            _f("email", default=False),
        ),
    ),
    BlockSpec(
        "meta",
        "meta",
        locked=True,
        fields=(
            _f("number", locked=True),
            _f("issue_date", locked=True),
            _f("reference"),
            _f("due_date"),
            _f("payment_terms", default=False),
            _f("client_number", default=False),
            _f("delivery_date", default=False),
            _f("period"),
        ),
    ),
    BlockSpec(
        "payment_box",
        "body",
        default=False,
        fields=(
            _f("amount"),
            _f("iban"),
            _f("account_name"),
            _f("description"),
        ),
    ),
    BlockSpec("intro", "body"),
    BlockSpec(
        "lines",
        "body",
        locked=True,
        fields=(
            _f("description", locked=True),
            _f("quantity"),
            _f("unit", default=False),
            _f("unit_price"),
            _f("tax"),
            _f("amount", locked=True),
        ),
    ),
    BlockSpec("tax_summary", "body", default=False),
    BlockSpec(
        "totals",
        "body",
        locked=True,
        fields=(
            _f("subtotal"),
            _f("tax_rows", locked=True),
            _f("total", locked=True),
            _f("paid"),
            _f("credited"),
            _f("to_pay"),
        ),
    ),
    # Not a preference: a reverse-charged line must say so on the paper. It is in the catalog
    # so a tenant can *move* it, and locked so they cannot drop the sentence that makes the
    # zero-rate lawful.
    BlockSpec("reverse_charge", "body", locked=True),
    BlockSpec("notes", "body"),
    BlockSpec("payment", "body"),
    BlockSpec("footer", "body"),
)

BLOCKS_BY_KEY: dict[str, BlockSpec] = {block.key: block for block in BLOCK_CATALOG}


@dataclass
class ResolvedField:
    key: str
    enabled: bool
    locked: bool
    #: The tenant's own wording for this field's label, per locale. Empty = the catalog's.
    label_i18n: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedBlock:
    key: str
    region: Region
    enabled: bool
    locked: bool
    fields: list[ResolvedField] = field(default_factory=list)

    def field_order(self) -> list[str]:
        """The enabled field keys, in print order — what a design iterates."""
        return [f.key for f in self.fields if f.enabled]

    def shows(self, key: str) -> bool:
        return any(f.key == key and f.enabled for f in self.fields)


@dataclass
class ResolvedLayout:
    blocks: dict[str, ResolvedBlock]
    #: Body blocks, enabled, in the order they print.
    body_order: list[str]

    def block(self, key: str) -> ResolvedBlock:
        resolved = self.blocks.get(key)
        if resolved is None:  # a design asking for a key the catalog dropped
            return ResolvedBlock(key=key, region="body", enabled=False, locked=False)
        return resolved

    def enabled(self, key: str) -> bool:
        return self.block(key).enabled

    def fields(self, key: str) -> list[str]:
        return self.block(key).field_order()

    def shows(self, block_key: str, field_key: str) -> bool:
        return self.block(block_key).shows(field_key)

    def label_i18n(self, block_key: str, field_key: str) -> dict[str, str]:
        """The tenant's own wording for a field's label, per locale. Empty = the catalog's.

        "Telefoon" and "t" are the same field; which one prints is the agency's letterhead,
        not ours. The catalog still owns the *key*, so an override is a display string and
        never widens what a template can name.
        """
        for resolved in self.block(block_key).fields:
            if resolved.key == field_key:
                return resolved.label_i18n
        return {}


def _order_by(stored_keys: list[str], catalog_keys: list[str]) -> list[str]:
    """Catalog keys, ordered by the stored layout, with the unmentioned kept in place.

    A key the layout names moves to where the layout puts it. A key it has never seen — one
    added by a later release, or one the tenant's editor simply did not rewrite — lands
    beside the keys it sits next to in the catalog, rather than being swept to the end. That
    is what lets a layout be a *partial* statement: naming three blocks reorders those three
    and leaves everything else where it was.

    Placement walks the catalog and inserts after the nearest preceding key **already
    placed** — checking the growing result, not only the stored list, so a run of consecutive
    new keys stays in catalog order instead of each landing at the front.
    """
    known = [key for key in stored_keys if key in catalog_keys]
    if not known:
        return list(catalog_keys)
    merged = list(known)
    for key in catalog_keys:
        if key in merged:
            continue
        catalog_index = catalog_keys.index(key)
        placed = [k for k in catalog_keys[:catalog_index] if k in merged]
        at = merged.index(placed[-1]) + 1 if placed else 0
        merged.insert(at, key)
    return merged


def _labels(entry: dict | None) -> dict[str, str]:
    """A stored field's ``label_i18n``, reduced to locale → non-empty string.

    Defensive because the config is tenant-writable JSONB: a blank string is *not* an
    override (it would print an empty column heading), and a non-string value is not one
    either.
    """
    raw = (entry or {}).get("label_i18n")
    if not isinstance(raw, dict):
        return {}
    return {
        str(locale): str(text).strip()
        for locale, text in raw.items()
        if isinstance(text, str) and text.strip()
    }


def resolve_layout(layout: list[dict] | None) -> ResolvedLayout:
    """Merge a stored layout onto the catalog. ``None``/empty = the catalog's own defaults."""
    stored = {
        str(entry.get("key")): entry
        for entry in (layout or [])
        if isinstance(entry, dict) and entry.get("key")
    }
    catalog_keys = [block.key for block in BLOCK_CATALOG]
    stored_order = [
        str(entry.get("key"))
        for entry in (layout or [])
        if isinstance(entry, dict) and entry.get("key")
    ]
    order = _order_by(stored_order, catalog_keys)

    blocks: dict[str, ResolvedBlock] = {}
    for key in order:
        spec = BLOCKS_BY_KEY[key]
        entry = stored.get(key)
        enabled = spec.default if entry is None else bool(entry.get("enabled", spec.default))
        resolved = ResolvedBlock(
            key=spec.key,
            region=spec.region,
            enabled=True if spec.locked else enabled,
            locked=spec.locked,
        )
        stored_fields = {
            str(item.get("key")): item
            for item in (entry or {}).get("fields") or []
            if isinstance(item, dict) and item.get("key")
        }
        field_keys = [f.key for f in spec.fields]
        for field_key in _order_by(
            [str(item.get("key")) for item in (entry or {}).get("fields") or []
             if isinstance(item, dict) and item.get("key")],
            field_keys,
        ):
            field_spec = next(f for f in spec.fields if f.key == field_key)
            item = stored_fields.get(field_key)
            default = field_spec.default
            field_enabled = default if item is None else bool(item.get("enabled", default))
            resolved.fields.append(
                ResolvedField(
                    key=field_spec.key,
                    enabled=True if field_spec.locked else field_enabled,
                    locked=field_spec.locked,
                    label_i18n=_labels(item) if field_spec.labelled else {},
                )
            )
        blocks[key] = resolved

    body_order = [key for key in order if blocks[key].region == "body" and blocks[key].enabled]
    return ResolvedLayout(blocks=blocks, body_order=body_order)


def catalog_payload() -> list[dict]:
    """The catalog as JSON for the settings editor — keys only; the client owns the labels.

    Labels are `invoicing.block.<key>` / `invoicing.field.<block>.<key>` in the *viewer's*
    locale, which is why the API does not resolve them (§17's rule: the API does not pick a
    locale for someone else's screen).
    """
    return [
        {
            "key": block.key,
            "region": block.region,
            "default": block.default,
            "locked": block.locked,
            "movable": block.movable,
            "fields": [
                {"key": f.key, "default": f.default, "locked": f.locked, "labelled": f.labelled}
                for f in block.fields
            ],
        }
        for block in BLOCK_CATALOG
    ]


def layout_from_legacy(*, show_logo: bool, columns: dict | None) -> list[dict]:
    """A pre-layout config's two knobs, expressed as a layout.

    ``show_logo`` and ``columns`` are what templates carried before this module existed. They
    stay the input while a template has no layout of its own, so upgrading the release does
    not silently redesign every document a tenant has already approved.
    """
    entries: list[dict] = [{"key": "logo", "enabled": bool(show_logo)}]
    if columns:
        spec = BLOCKS_BY_KEY["lines"]
        entries.append(
            {
                "key": "lines",
                "enabled": True,
                "fields": [
                    {"key": f.key, "enabled": bool(columns.get(f.key, f.default))}
                    if f.key in columns
                    else {"key": f.key, "enabled": f.default}
                    for f in spec.fields
                ],
            }
        )
    return entries

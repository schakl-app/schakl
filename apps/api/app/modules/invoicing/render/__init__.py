"""Document rendering (issue #207 follow-up): one HTML artefact, previewed and printed.

The module's public surface, so callers never reach into the internals:

* :func:`render_document_html` — the document as a standalone page. This is what the preview
  iframe shows and what :func:`render_document_pdf` prints, which is the whole point: there
  is no second implementation to keep in step with the first.
* :func:`render_document_pdf` — the same HTML, through WeasyPrint.
* :class:`DocumentBrand` — the white-label identity the caller resolves and passes in
  (Golden Rule 4: nothing below here owns a default logo, colour or name).
* :data:`BLOCK_CATALOG` / :func:`catalog_payload` — what a template may rearrange (§blocks).
"""

from __future__ import annotations

from typing import Any

from app.modules.invoicing.render.blocks import (
    BLOCK_CATALOG,
    BLOCKS_BY_KEY,
    catalog_payload,
    layout_from_legacy,
    resolve_layout,
)
from app.modules.invoicing.render.colors import accent_for, document_accent
from app.modules.invoicing.render.context import DocumentBrand, build_context
from app.modules.invoicing.render.engine import (
    BUILTIN_DESIGNS,
    DEFAULT_DESIGN,
    MAX_CUSTOM_CSS,
    MAX_CUSTOM_HTML,
    TemplateRenderError,
    builtin_source,
    html_to_pdf,
    render_html,
    validate_custom_source,
)

__all__ = [
    "BLOCKS_BY_KEY",
    "BLOCK_CATALOG",
    "BUILTIN_DESIGNS",
    "DEFAULT_DESIGN",
    "MAX_CUSTOM_CSS",
    "MAX_CUSTOM_HTML",
    "DocumentBrand",
    "TemplateRenderError",
    "accent_for",
    "builtin_source",
    "catalog_payload",
    "document_accent",
    "layout_from_legacy",
    "render_document_html",
    "render_document_pdf",
    "resolve_layout",
    "validate_custom_source",
]


def render_document_html(
    *,
    kind: str,
    doc: Any,
    lines: list[Any],
    seller: dict[str, Any],
    config: dict[str, Any],
    brand: DocumentBrand,
    tax_groups: list[Any] | None = None,
) -> str:
    context = build_context(
        kind=kind,
        doc=doc,
        lines=lines,
        seller=seller,
        config=config,
        brand=brand,
        tax_groups=tax_groups,
    )
    return render_html(context, config or {})


def render_document_pdf(
    *,
    kind: str,
    doc: Any,
    lines: list[Any],
    seller: dict[str, Any],
    config: dict[str, Any],
    brand: DocumentBrand,
    tax_groups: list[Any] | None = None,
) -> bytes:
    """Blocking and CPU-bound — callers run it in a thread (``asyncio.to_thread``)."""
    html = render_document_html(
        kind=kind,
        doc=doc,
        lines=lines,
        seller=seller,
        config=config,
        brand=brand,
        tax_groups=tax_groups,
    )
    return html_to_pdf(html, locale=getattr(doc, "locale", None) or "nl")

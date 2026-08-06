"""The invoice family's binding of the shared document engine (``app.core.documents``).

The renderer itself — the sandbox, the no-network URL fetcher, the two Jinja environments,
page numbering, the size caps — moved to core when the reporting module needed the same one
(issue #300). What stays here is the part that is genuinely invoicing's: *these* designs,
*these* i18n keys.

Every name this module used to export still resolves, with the same signature, so nothing
downstream had to change: ``service.py`` and ``router.py`` import through ``render/__init__``
exactly as before, and the walls the tests assert on are the same objects, now shared.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.documents.engine import (
    MAX_CUSTOM_CSS,
    MAX_CUSTOM_HTML,
    DocumentEngine,
    TemplateRenderError,
    no_network_fetcher,
    page_number_css,
)

#: The refusal wall under its old private name — ``tests/test_invoicing_render.py`` asserts on
#: it through this module path, and it is the same object it always was, now shared.
_no_network_fetcher = no_network_fetcher

DESIGNS_DIR = Path(__file__).parent / "designs"

#: The designs a template may name. ``custom`` is not here: it renders the tenant's own HTML.
BUILTIN_DESIGNS: tuple[str, ...] = ("classic", "letterhead")
DEFAULT_DESIGN = "classic"

ENGINE = DocumentEngine(
    designs_dir=DESIGNS_DIR,
    builtin_designs=BUILTIN_DESIGNS,
    default_design=DEFAULT_DESIGN,
    page_key="invoicing.doc.page",
    render_error_key="errors.invoicing.template_render",
    too_large_key="errors.invoicing.template_too_large",
)

__all__ = [
    "BUILTIN_DESIGNS",
    "DEFAULT_DESIGN",
    "DESIGNS_DIR",
    "ENGINE",
    "MAX_CUSTOM_CSS",
    "MAX_CUSTOM_HTML",
    "TemplateRenderError",
    "builtin_source",
    "html_to_pdf",
    "render_html",
    "validate_custom_source",
]


def _page_number_css(locale: str) -> str:
    return page_number_css(ENGINE.page_key, locale)


def render_html(context: dict[str, Any], config: dict[str, Any]) -> str:
    """The document as a standalone HTML page — previewed as-is, printed as-is."""
    return ENGINE.render_html(context, config)


def html_to_pdf(html: str, *, locale: str = "nl") -> bytes:
    """Print the document HTML. Blocking and CPU-bound — call it in a thread."""
    return ENGINE.html_to_pdf(html, locale=locale)


def validate_custom_source(html: str | None, css: str | None) -> None:
    """Refuse a template body that cannot render, at *save* time."""
    ENGINE.validate_custom_source(html, css)


def builtin_source(design: str) -> tuple[str, str]:
    """A shipped design's ``(html, css)`` — what "start from this one" hands the author."""
    return ENGINE.builtin_source(design)

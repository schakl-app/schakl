"""Printed documents — one renderer, every family (issue #300).

A *document family* is a kind of paper the platform prints: an invoice, a quote, a monthly
client report. Each brings its own Jinja designs, its own context builder and its own i18n
keys; all of them share this engine, its sandbox, its no-network URL fetcher, its page
numbering and its charts.

This is core rather than a module because CLAUDE.md §6 forbids a module importing another
module's internals, and "print this HTML as A4" is not invoicing's question. The alternative —
a second copy in the reporting module — is the mistake ``docs/INVOICING.md`` opens by saying
was already corrected once.

    from app.core.documents import ChartStyle, DocumentEngine, grouped_columns

    ENGINE = DocumentEngine(
        designs_dir=Path(__file__).parent / "designs",
        builtin_designs=("classic",),
        default_design="classic",
        page_key="reporting.doc.page",
        render_error_key="errors.reporting.template_render",
        too_large_key="errors.reporting.template_too_large",
    )
"""

from __future__ import annotations

from app.core.documents.charts import (
    ChartStyle,
    Formatter,
    column_chart,
    grouped_columns,
    share_bar,
    sparkline,
)
from app.core.documents.colors import (
    DEFAULT_ACCENT,
    INK,
    MUTED,
    RGB,
    RULE,
    WASH,
    accent_for,
    document_accent,
    hex_rgb,
    mix_on_white,
    rgb_hex,
    rgba,
)
from app.core.documents.engine import (
    MAX_CUSTOM_CSS,
    MAX_CUSTOM_HTML,
    DocumentEngine,
    TemplateRenderError,
    html_to_pdf,
    no_network_fetcher,
    page_number_css,
)

__all__ = [
    "DEFAULT_ACCENT",
    "INK",
    "MAX_CUSTOM_CSS",
    "MAX_CUSTOM_HTML",
    "MUTED",
    "RGB",
    "RULE",
    "WASH",
    "ChartStyle",
    "DocumentEngine",
    "Formatter",
    "TemplateRenderError",
    "accent_for",
    "column_chart",
    "document_accent",
    "grouped_columns",
    "hex_rgb",
    "html_to_pdf",
    "mix_on_white",
    "no_network_fetcher",
    "page_number_css",
    "rgb_hex",
    "rgba",
    "share_bar",
    "sparkline",
]

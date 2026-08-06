"""The report family's binding of the shared document engine (``app.core.documents``).

Second caller of the engine invoicing already uses, which is why that engine is core (#300).
What differs between the two families is exactly what a :class:`DocumentEngine` takes: this
directory of designs, this page-footer catalog key, this error envelope.
"""

from __future__ import annotations

from pathlib import Path

from app.core.documents import DocumentEngine

DESIGNS_DIR = Path(__file__).parent / "designs"

#: ``custom`` is never here — it renders the tenant's own Jinja inside our shell.
BUILTIN_DESIGNS: tuple[str, ...] = ("standard",)
DEFAULT_DESIGN = "standard"

ENGINE = DocumentEngine(
    designs_dir=DESIGNS_DIR,
    builtin_designs=BUILTIN_DESIGNS,
    default_design=DEFAULT_DESIGN,
    page_key="reporting.doc.page",
    render_error_key="errors.reporting.template_render",
    too_large_key="errors.reporting.template_too_large",
)

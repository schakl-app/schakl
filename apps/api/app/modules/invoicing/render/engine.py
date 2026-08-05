"""HTML in, document out — and the walls around a tenant-authored template.

One renderer draws every document. The built-in designs are Jinja templates shipped in
``designs/``; a tenant's own design is *their* Jinja, rendered against the same context
(``context.build_context``). The HTML this produces is what the browser previews **and** what
WeasyPrint prints, so a preview and its PDF cannot drift — the old pair of hand-kept
implementations (an fpdf script and a Svelte component) is what this replaces.

A tenant admin authoring a template is writing code that runs on the agency's server, so:

* **The environment is sandboxed.** ``SandboxedEnvironment`` refuses attribute traversal into
  Python internals, so ``{{ ''.__class__.__mro__ }}`` — the standard escape from a naive
  Jinja setup to arbitrary imports — raises instead of resolving.
* **Nothing is fetched.** WeasyPrint resolves external URLs through a ``url_fetcher``; ours
  answers ``data:`` only and raises on every other scheme. That is one rule covering
  ``file:///etc/passwd``, ``http://169.254.169.254/`` and a slow CDN alike. The images a
  document legitimately shows (logo, background mark) are inlined as data URIs upstream, so
  the built-in designs never need the network either.
* **A template cannot hang the worker.** Renders are bounded in output size, and the loader
  refuses ``{% include %}``/``{% extends %}`` so a custom template cannot pull in a design it
  was not given.

Rendering is CPU-bound and blocking; callers run it off the event loop
(``asyncio.to_thread``), the rule the storage routes already follow.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from app.errors import AppError
from app.modules.invoicing.render.colors import MUTED, rgb_hex

logger = logging.getLogger("schakl.invoicing")

DESIGNS_DIR = Path(__file__).parent / "designs"

#: The designs a template may name. ``custom`` is not here: it renders the tenant's own HTML.
BUILTIN_DESIGNS: tuple[str, ...] = ("classic", "letterhead")
DEFAULT_DESIGN = "classic"

#: A tenant's template body and stylesheet are bounded — a megabyte of CSS is not a design,
#: and the value is stored in a JSONB column every document read touches.
MAX_CUSTOM_HTML = 200_000
MAX_CUSTOM_CSS = 100_000


class TemplateRenderError(AppError):
    """A tenant's own template failed to render. Carries the Jinja message, not a traceback."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            "validation",
            "errors.invoicing.template_render",
            status_code=422,
            fields={"html": detail[:500]},
        )


def _no_network_fetcher(url: str, *args: Any, **kwargs: Any) -> dict:
    """WeasyPrint's URL resolver, reduced to ``data:``.

    Every image a document shows is inlined upstream, so a request for any other scheme is
    either a mistake or an attempt to make the server fetch something — a local file, a cloud
    metadata endpoint, a URL that proves the invoice was opened. Refuse all of it.
    """
    if url.startswith("data:"):
        from weasyprint.urls import default_url_fetcher

        return default_url_fetcher(url, *args, **kwargs)
    logger.info("document template requested a non-data URL; refused (%s)", url[:80])
    raise ValueError("external resources are not available to document templates")


@lru_cache(maxsize=1)
def _builtin_environment() -> Environment:
    """Our own designs. Not sandboxed — this is first-party code in the image, and it needs
    ``{% include %}`` to share the macros both designs draw from."""
    env = Environment(
        loader=FileSystemLoader(DESIGNS_DIR),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["nl2br"] = _nl2br
    env.filters["css"] = _css
    return env


@lru_cache(maxsize=1)
def _custom_environment() -> SandboxedEnvironment:
    """A tenant's own design. No loader at all, so ``{% include 'letterhead.html' %}`` (or
    anything else on disk) has nothing to resolve against."""
    env = SandboxedEnvironment(
        loader=None,
        autoescape=True,
        # Not StrictUndefined: a tenant writing `{{ customer.phone }}` for a customer who has
        # none should get a blank, not a 422 on an invoice they are trying to send.
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["nl2br"] = _nl2br
    env.filters["css"] = _css
    return env


def _nl2br(value: Any) -> str:
    """Newlines to ``<br>`` for the entered blocks (an address, an intro paragraph)."""
    from markupsafe import Markup, escape

    return Markup("<br>".join(escape(str(value or "")).split("\n")))


_STYLE_CLOSE = re.compile(r"</\s*style", re.IGNORECASE)


def _css(value: Any) -> str:
    """Tenant CSS, emitted into a ``<style>`` element without being able to leave it.

    Escaping it as text would break every ``>`` and quote in a real stylesheet, so it goes in
    raw — with the one sequence that ends the element neutralised. A stylesheet that closes
    its own tag is writing markup, and this is the boundary that says it may not.
    """
    from markupsafe import Markup

    return Markup(_STYLE_CLOSE.sub("<\\\\/style", str(value or "")))


@lru_cache(maxsize=1)
def _block_macros() -> Any:
    """``_blocks.html``'s macros as a callable module, handed to templates via the context.

    Reaching them through the context rather than ``{% import %}`` is what makes a design's
    body file *portable*: the same markup runs as a shipped design and as the starting point
    of a tenant's own template, which has no loader to import from. The macros only
    interpolate what they are given, and the environment they were compiled in autoescapes,
    so calling them from the sandbox renders strings — it does not widen it.
    """
    return _builtin_environment().get_template("_blocks.html").module


def _augmented(context: dict[str, Any]) -> dict[str, Any]:
    """The context plus the two names every design needs: itself, and the block macros."""
    # `ctx` is the same dict again under one name, so a design can hand the whole context to
    # a block macro and dispatch over `body_order` in a single loop.
    return {**context, "ctx": context, "blocks": _block_macros()}


def render_html(context: dict[str, Any], config: dict[str, Any]) -> str:
    """The document as a standalone HTML page — previewed as-is, printed as-is."""
    design = str(config.get("design") or DEFAULT_DESIGN)
    css = str(config.get("css") or "")[:MAX_CUSTOM_CSS]
    if design == "custom":
        return _render_custom(context, config, css)
    if design not in BUILTIN_DESIGNS:
        design = DEFAULT_DESIGN
    template = _builtin_environment().get_template(f"{design}.html")
    return template.render(**_augmented(context), custom_css=css)


def _render_custom(context: dict[str, Any], config: dict[str, Any], css: str) -> str:
    body = str(config.get("html") or "")[:MAX_CUSTOM_HTML]
    if not body.strip():
        # An empty custom design would print a blank page and look like data loss. Fall back
        # to the design it was branched from and say nothing — the editor already refuses to
        # save an empty body, so reaching here means a config edited by hand.
        template = _builtin_environment().get_template(f"{DEFAULT_DESIGN}.html")
        return template.render(**_augmented(context), custom_css=css)
    try:
        rendered = _custom_environment().from_string(body).render(**_augmented(context))
    except TemplateError as exc:
        raise TemplateRenderError(f"{type(exc).__name__}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — a sandbox refusal is the author's error too
        raise TemplateRenderError(f"{type(exc).__name__}: {exc}") from exc
    shell = _builtin_environment().get_template("custom.html")
    return shell.render(**_augmented(context), custom_body=rendered, custom_css=css)


def _page_number_css(locale: str) -> str:
    """``2 / 3`` bottom-right, built from the catalog key rather than hardcoded.

    ``invoicing.doc.page`` is ``{page} / {total}``; splitting it on its own placeholders is
    what lets a locale that writes "Pagina 2 van 3" print that, from CSS counters that cannot
    interpolate. Sentinels rather than a regex because the separator is the translated text.
    """
    from app.i18n import translate

    pattern = translate("invoicing.doc.page", locale, page="\x00", total="\x01")
    parts = pattern.replace("\x01", "\x00").split("\x00")
    counters = ("counter(page)", "counter(pages)")
    pieces: list[str] = []
    for index, literal in enumerate(parts):
        if literal:
            escaped = literal.replace("\\", "\\\\").replace('"', '\\"')
            pieces.append(f'"{escaped}"')
        if index < len(parts) - 1:
            pieces.append(counters[min(index, 1)])
    content = " ".join(pieces) or "counter(page)"
    rule = f"content: {content}; font-size: 7.5pt; color: {rgb_hex(MUTED)};"
    return f"@page {{ @bottom-right {{ {rule} }} }}"


def html_to_pdf(html: str, *, locale: str = "nl") -> bytes:
    """Print the document HTML. Blocking and CPU-bound — call it in a thread.

    Page numbers arrive as a *second* stylesheet rather than living in the design, so a
    one-page invoice stays unnumbered: "1 / 1" in the corner of a single sheet is noise. That
    costs a second layout pass, and only on documents that actually run to two pages.
    """
    from weasyprint import CSS, HTML

    def document(stylesheets: list | None = None):  # noqa: ANN202 — weasyprint's own type
        return HTML(string=html, url_fetcher=_no_network_fetcher, base_url=None).render(
            stylesheets=stylesheets
        )

    rendered = document()
    if len(rendered.pages) < 2:
        return rendered.write_pdf()
    numbering = CSS(string=_page_number_css(locale), url_fetcher=_no_network_fetcher)
    return document([numbering]).write_pdf()


def validate_custom_source(html: str | None, css: str | None) -> None:
    """Refuse a template body that cannot render, at *save* time.

    A syntax error caught here is a red field under an editor the author is looking at; the
    same error caught at send time is an invoice that will not go out, discovered by whoever
    was trying to send it. Rendering against a sample context is the only check that finds it.
    """
    if html is None and css is None:
        return
    if html is not None and len(html) > MAX_CUSTOM_HTML:
        raise AppError(
            "validation", "errors.invoicing.template_too_large", status_code=422,
            fields={"html": "errors.invoicing.template_too_large"},
        )
    if css is not None and len(css) > MAX_CUSTOM_CSS:
        raise AppError(
            "validation", "errors.invoicing.template_too_large", status_code=422,
            fields={"css": "errors.invoicing.template_too_large"},
        )
    if not (html or "").strip():
        return
    try:
        _custom_environment().from_string(html or "")
    except TemplateError as exc:
        raise TemplateRenderError(f"{type(exc).__name__}: {exc}") from exc


def builtin_source(design: str) -> tuple[str, str]:
    """A shipped design's ``(html, css)`` — what "start from this one" hands the author.

    Writing a document template from a blank page means knowing the whole context by heart.
    Branching from the design they already like means changing the two things they want
    changed, which is what a tenant actually wants from "bring your own template". These are
    the *same* files the shipped design renders from, so what they get is what they saw.
    """
    name = design if design in BUILTIN_DESIGNS else DEFAULT_DESIGN
    return (
        (DESIGNS_DIR / f"{name}.body.html").read_text(encoding="utf-8"),
        (DESIGNS_DIR / f"{name}.css").read_text(encoding="utf-8"),
    )

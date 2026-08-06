"""HTML in, document out — and the walls around a tenant-authored template.

Promoted out of ``modules/invoicing/render/engine.py`` when reporting needed the same
renderer (issue #300). The reason it had to move rather than be copied is written at the top
of ``docs/INVOICING.md``: *"There used to be two renderers… each carried a comment telling you
to keep it in step with the other."* A second copy in a second module is that mistake again,
one layer out — and CLAUDE.md §6 forbids the reporting module from importing invoicing's
internals, so "just import it" was never available either.

What is generic (here) and what is not (the caller's):

* **Generic** — the sandbox, the no-network URL fetcher, the two Jinja environments, the
  ``{% include %}``-less custom environment, page numbering from a catalog key, the size caps,
  and "start from this design" source extraction.
* **The caller's** — its own ``designs/`` directory, its own built-in design names, its own
  i18n keys for the page footer and its error envelope. Those arrive as a
  :class:`DocumentEngine`, one per document family: invoicing has one, reporting has one.

A tenant admin authoring a template is writing code that runs on the agency's server, so:

* **The environment is sandboxed.** ``SandboxedEnvironment`` refuses attribute traversal into
  Python internals, so ``{{ ''.__class__.__mro__ }}`` — the standard escape from a naive
  Jinja setup to arbitrary imports — raises instead of resolving.
* **Nothing is fetched.** WeasyPrint resolves external URLs through a ``url_fetcher``; ours
  answers ``data:`` only and raises on every other scheme. That is one rule covering
  ``file:///etc/passwd``, ``http://169.254.169.254/`` and a slow CDN alike. The images a
  document legitimately shows (logo, background mark, a chart) are inlined as data URIs or
  written as inline SVG upstream, so the built-in designs never need the network either.
* **A template cannot hang the worker.** Renders are bounded in output size, and the loader
  refuses ``{% include %}``/``{% extends %}`` so a custom template cannot pull in a design it
  was not given.

Rendering is CPU-bound and blocking; callers run it off the event loop
(``asyncio.to_thread``), the rule the storage routes already follow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from app.core.documents.colors import MUTED, rgb_hex
from app.errors import AppError

logger = logging.getLogger("schakl.documents")

#: A tenant's template body and stylesheet are bounded — a megabyte of CSS is not a design,
#: and the value is stored in a JSONB column every document read touches.
MAX_CUSTOM_HTML = 200_000
MAX_CUSTOM_CSS = 100_000


class TemplateRenderError(AppError):
    """A tenant's own template failed to render. Carries the Jinja message, not a traceback."""

    def __init__(self, detail: str, message_key: str) -> None:
        super().__init__(
            "validation",
            message_key,
            status_code=422,
            fields={"html": detail[:500]},
        )


def no_network_fetcher(url: str, *args: Any, **kwargs: Any) -> dict:
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


@lru_cache(maxsize=8)
def _builtin_environment(designs_dir: str) -> Environment:
    """One document family's own designs. Not sandboxed — this is first-party code in the
    image, and it needs ``{% include %}`` to share the macros the designs draw from."""
    env = Environment(
        loader=FileSystemLoader(designs_dir),
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
    anything else on disk) has nothing to resolve against.

    One environment for every document family on purpose: it has no loader, so it carries no
    family-specific state, and a second instance would only be a second cache to warm.
    """
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


@lru_cache(maxsize=8)
def _block_macros(designs_dir: str) -> Any:
    """``_blocks.html``'s macros as a callable module, handed to templates via the context.

    Reaching them through the context rather than ``{% import %}`` is what makes a design's
    body file *portable*: the same markup runs as a shipped design and as the starting point
    of a tenant's own template, which has no loader to import from. The macros only
    interpolate what they are given, and the environment they were compiled in autoescapes,
    so calling them from the sandbox renders strings — it does not widen it.

    ``None`` for a design set that ships no macro file; a design that never calls ``blocks.*``
    should not be forced to carry an empty one.
    """
    if not (Path(designs_dir) / "_blocks.html").exists():
        return None
    return _builtin_environment(designs_dir).get_template("_blocks.html").module


def page_number_css(page_key: str, locale: str) -> str:
    """``2 / 3`` bottom-right, built from the catalog key rather than hardcoded.

    The key resolves to ``{page} / {total}``; splitting it on its own placeholders is what
    lets a locale that writes "Pagina 2 van 3" print that, from CSS counters that cannot
    interpolate. Sentinels rather than a regex because the separator is the translated text.
    """
    from app.i18n import translate

    pattern = translate(page_key, locale, page="\x00", total="\x01")
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


def html_to_pdf(html: str, *, page_key: str, locale: str = "nl") -> bytes:
    """Print a document HTML page. Blocking and CPU-bound — call it in a thread.

    Page numbers arrive as a *second* stylesheet rather than living in the design, so a
    one-page document stays unnumbered: "1 / 1" in the corner of a single sheet is noise. That
    costs a second layout pass, and only on documents that actually run to two pages.
    """
    from weasyprint import CSS, HTML

    def document(stylesheets: list | None = None):  # noqa: ANN202 — weasyprint's own type
        return HTML(string=html, url_fetcher=no_network_fetcher, base_url=None).render(
            stylesheets=stylesheets
        )

    rendered = document()
    if len(rendered.pages) < 2:
        return rendered.write_pdf()
    numbering = CSS(string=page_number_css(page_key, locale), url_fetcher=no_network_fetcher)
    return document([numbering]).write_pdf()


@dataclass(frozen=True)
class DocumentEngine:
    """One document family's renderer: its designs, its i18n keys, its error envelope.

    Frozen and cheap — construct one at module import (``invoicing.render``,
    ``reporting.render``) and call it. All state that is worth caching is keyed on
    ``designs_dir`` in the module-level caches above, so several engines over the same
    directory share one compiled environment.
    """

    #: Where this family's Jinja designs live.
    designs_dir: Path
    #: The designs a template may name. ``custom`` is never here: it renders tenant HTML.
    builtin_designs: tuple[str, ...]
    default_design: str
    #: Catalog key for the page footer, ``{page} / {total}``.
    page_key: str
    #: Error-envelope message keys (CLAUDE.md §9 — a message is an i18n key).
    render_error_key: str
    too_large_key: str

    @property
    def _dir(self) -> str:
        return str(self.designs_dir)

    def _augmented(self, context: dict[str, Any]) -> dict[str, Any]:
        """The context plus the two names every design needs: itself, and the block macros."""
        # `ctx` is the same dict again under one name, so a design can hand the whole context
        # to a block macro and dispatch over an ordered body in a single loop.
        return {**context, "ctx": context, "blocks": _block_macros(self._dir)}

    def render_html(self, context: dict[str, Any], config: dict[str, Any]) -> str:
        """The document as a standalone HTML page — previewed as-is, printed as-is."""
        design = str(config.get("design") or self.default_design)
        css = str(config.get("css") or "")[:MAX_CUSTOM_CSS]
        if design == "custom":
            return self._render_custom(config, css, context)
        if design not in self.builtin_designs:
            design = self.default_design
        template = _builtin_environment(self._dir).get_template(f"{design}.html")
        return template.render(**self._augmented(context), custom_css=css)

    def _render_custom(
        self, config: dict[str, Any], css: str, context: dict[str, Any]
    ) -> str:
        body = str(config.get("html") or "")[:MAX_CUSTOM_HTML]
        if not body.strip():
            # An empty custom design would print a blank page and look like data loss. Fall
            # back to the design it was branched from and say nothing — the editor already
            # refuses to save an empty body, so reaching here means a config edited by hand.
            template = _builtin_environment(self._dir).get_template(
                f"{self.default_design}.html"
            )
            return template.render(**self._augmented(context), custom_css=css)
        try:
            rendered = _custom_environment().from_string(body).render(**self._augmented(context))
        except TemplateError as exc:
            raise TemplateRenderError(
                f"{type(exc).__name__}: {exc}", self.render_error_key
            ) from exc
        except Exception as exc:  # noqa: BLE001 — a sandbox refusal is the author's error too
            raise TemplateRenderError(
                f"{type(exc).__name__}: {exc}", self.render_error_key
            ) from exc
        shell = _builtin_environment(self._dir).get_template("custom.html")
        return shell.render(**self._augmented(context), custom_body=rendered, custom_css=css)

    def html_to_pdf(self, html: str, *, locale: str = "nl") -> bytes:
        return html_to_pdf(html, page_key=self.page_key, locale=locale)

    def validate_custom_source(self, html: str | None, css: str | None) -> None:
        """Refuse a template body that cannot render, at *save* time.

        A syntax error caught here is a red field under an editor the author is looking at;
        the same error caught at send time is a document that will not go out, discovered by
        whoever was trying to send it. Rendering against a sample context is the only check
        that finds it.
        """
        if html is None and css is None:
            return
        if html is not None and len(html) > MAX_CUSTOM_HTML:
            raise AppError(
                "validation", self.too_large_key, status_code=422,
                fields={"html": self.too_large_key},
            )
        if css is not None and len(css) > MAX_CUSTOM_CSS:
            raise AppError(
                "validation", self.too_large_key, status_code=422,
                fields={"css": self.too_large_key},
            )
        if not (html or "").strip():
            return
        try:
            _custom_environment().from_string(html or "")
        except TemplateError as exc:
            raise TemplateRenderError(
                f"{type(exc).__name__}: {exc}", self.render_error_key
            ) from exc

    def builtin_source(self, design: str) -> tuple[str, str]:
        """A shipped design's ``(html, css)`` — what "start from this one" hands the author.

        Writing a document template from a blank page means knowing the whole context by
        heart. Branching from the design they already like means changing the two things they
        want changed, which is what a tenant actually wants from "bring your own template".
        These are the *same* files the shipped design renders from, so what they get is what
        they saw.
        """
        name = design if design in self.builtin_designs else self.default_design
        return (
            (self.designs_dir / f"{name}.body.html").read_text(encoding="utf-8"),
            (self.designs_dir / f"{name}.css").read_text(encoding="utf-8"),
        )

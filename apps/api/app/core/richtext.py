"""Rich-text (markdown) safety helpers — the API half of issue #66 (CLAUDE.md §8).

Long-form user text is authored as **markdown** and stored as its source (never pre-rendered
HTML): storing the source keeps every row greppable and lets a future sanitizer fix protect
content written today, which pre-rendered HTML could not. The web renders it — sanitized — through
one shared component that is the only place markup becomes markup (docs/UX.md).

This module is the *write* half of a defence-in-depth pair; the render-side DOMPurify pass is the
other. Neither alone is enough — the renderer is the authoritative XSS boundary, but a stored value
that never carries raw HTML also protects the consumers that render it *without* going through the
web (a future email/PDF path, CLAUDE.md §8):

* :func:`sanitize_markdown` strips raw HTML from the source on write. Markdown needs no tags of its
  own, so an empty allow-list removes ``<script>``/``<img onerror>`` while leaving markdown syntax
  (``**``, ``[]()``, ``#`` …) untouched. It is **idempotent** — an already-escaped ``&amp;`` is left
  alone rather than doubled — so a field edited many times never escalates.
* :func:`markdown_to_plaintext` flattens markdown to readable text for the consumers that must show
  the words but not the syntax: the notification excerpt today (a raw ``**bold**`` in the bell
  dropdown reads as noise), and emails/PDFs later.
"""

from __future__ import annotations

import re

import nh3


def sanitize_markdown(value: str | None) -> str | None:
    """Strip any raw HTML from markdown *source* on write; leave markdown syntax intact.

    ``None`` in, ``None`` out — the fields this guards are nullable. An empty tag/attribute
    allow-list makes nh3 drop every tag (and the contents of ``<script>``/``<style>``) while
    keeping the surrounding text, which is exactly a "no raw HTML in markdown" policy.
    """
    if value is None:
        return None
    return nh3.clean(value, tags=set(), attributes={})


# Applied in order to collapse the common inline/block markdown constructs to their text. This is
# deliberately a small regex pass, not a full parser: the goal is a *readable* excerpt, not a
# faithful render, and pulling a markdown dependency into the API just to shorten a notification
# would be the wrong trade (docs/PERFORMANCE.md). Links resolve to their text before any length
# cap runs, so truncation can never sever ``[label](url)`` mid-syntax (issue #66).
_MD_STRIP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),          # image -> alt text
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),           # link -> link text
    (re.compile(r"(\*\*|__)(.+?)\1"), r"\2"),               # bold
    (re.compile(r"(?<![\w*])[*_](?=\S)(.+?)(?<=\S)[*_]"), r"\1"),  # italic
    (re.compile(r"~~(.+?)~~"), r"\1"),                      # strikethrough
    (re.compile(r"`+([^`]*)`+"), r"\1"),                    # inline code
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE), ""),   # ATX headings
    (re.compile(r"^\s{0,3}>\s?", re.MULTILINE), ""),        # blockquote marker
    (re.compile(r"^\s{0,3}(?:[-*+]|\d+\.)\s+", re.MULTILINE), ""),  # list markers
)


def markdown_to_plaintext(value: str) -> str:
    """Flatten markdown to plain text (syntax removed, words kept). Not a renderer."""
    text = value
    for pattern, repl in _MD_STRIP:
        text = pattern.sub(repl, text)
    return text

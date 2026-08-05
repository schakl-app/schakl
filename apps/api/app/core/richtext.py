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
import uuid

import nh3

# `@[Display Name](mention:<uuid>)` — the marker the web's RichTextEditor writes (issue #63).
# The name is display-only; the id is the truth, so a rename never breaks who was mentioned.
# Shared here because more than one module parses it (task comments, contactmoment notes #151);
# two drifting copies of this regex would disagree about who got mentioned.
# An optional kind prefix (#165) — `mention:contact:<uuid>` — discriminates contact mentions;
# an absent prefix means a colleague, so every marker stored before #165 keeps parsing as one.
# A task reference (#197) is the same marker family with a `#` trigger and a `task` kind:
# `#[Task title](mention:task:<uuid>)` — a cross-link, never a notification recipient.
MENTION_RE = re.compile(
    r"[@#]\[[^\]]+\]\(mention:(?:(user|contact|task):)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)"
)


def _mention_ids(body: str | None, kind: str) -> list[uuid.UUID]:
    seen: dict[uuid.UUID, None] = {}
    for match in MENTION_RE.finditer(body or ""):
        if (match.group(1) or "user") != kind:
            continue
        try:
            seen.setdefault(uuid.UUID(match.group(2)), None)
        except ValueError:
            continue
    return list(seen)


def extract_mention_ids(body: str | None) -> list[uuid.UUID]:
    """The distinct *user* ids mentioned in a body, in first-seen order.

    Purely syntactic — the caller still validates the ids against org membership, so a stray
    or cross-tenant uuid can never notify anyone (issue #63).
    """
    return _mention_ids(body, "user")


def extract_contact_mention_ids(body: str | None) -> list[uuid.UUID]:
    """The distinct *contact* ids mentioned in a body (#165) — same syntactic-only contract;
    callers validate against the org's contacts."""
    return _mention_ids(body, "contact")


def extract_task_mention_ids(body: str | None) -> list[uuid.UUID]:
    """The distinct *task* ids referenced in a body (#197) — same syntactic-only contract;
    callers validate against the org's tasks. A reference is a deep link, never a
    notification: nobody is alerted because a task was linked."""
    return _mention_ids(body, "task")


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
# faithful render — a notification bell does not want a bullet list, it wants one line of words.
# (The API does now carry a real markdown parser, for :func:`markdown_to_html`; this pass stays
# because flattening and rendering are different jobs.) Links resolve to their text before any
# length cap runs, so truncation can never sever ``[label](url)`` mid-syntax (issue #66).
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


def markdown_excerpt(value: str | None, limit: int) -> str | None:
    """A teaser of a markdown body: flattened, single-spaced, cut to ``limit`` characters.

    The preview for text somebody wrote *here*, as opposed to the ``snippet`` an e-mail source
    hands us — a notification line, a contactmoment's timeline row. Flattening runs **before**
    the cap for two reasons that are the same reason: a cut by character count could sever a
    ``[label](url)`` mid-syntax, and a teaser reading ``**Afgesproken**`` shows syntax where it
    promised words. ``None`` when there is nothing readable left, so a caller can fall back.
    """
    if not value:
        return None
    text = " ".join(markdown_to_plaintext(value).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


#: What rendered markdown may contain on a document. No ``img`` and no ``iframe``: the
#: document renderer inlines the images *it* chose as data URIs and refuses every other
#: fetch, so an ``<img>`` here could only ever be a request we do not want to make.
_RENDER_TAGS = frozenset(
    {
        "p", "br", "strong", "em", "del", "code", "pre", "blockquote",
        "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
        "a", "table", "thead", "tbody", "tr", "th", "td",
    }
)
_RENDER_ATTRS = {"a": {"href", "title"}}


def markdown_to_html(value: str | None) -> str:
    """Render stored markdown to sanitized HTML, for the consumers that draw markup.

    The document renderer is the caller: an invoice's notes are authored as markdown (#66),
    and a PDF that prints literal ``**`` has not rendered them. Sanitizing here as well as on
    write is deliberate — :func:`sanitize_markdown` guards what we store, this guards what we
    emit, and a row written before that guard existed still cannot inject markup into a
    document. Links keep only ``href``/``title``, and nh3 rewrites unsafe schemes.
    """
    if not value or not value.strip():
        return ""
    from markdown_it import MarkdownIt

    rendered = MarkdownIt("commonmark").enable("table").render(value)
    return nh3.clean(rendered, tags=set(_RENDER_TAGS), attributes=dict(_RENDER_ATTRS))

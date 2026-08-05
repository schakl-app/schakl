"""HTML → markdown, for the bodies we receive rather than the ones we author.

An e-mail arrives as HTML and was being reduced to ``_TAG_RE.sub(" ", html)`` — every list,
heading, link, emphasis and quote level gone, and the result rendered as pre-wrapped plain
text. This keeps the structure by converting to **markdown source**, which is what the app
already stores for long-form text and already renders through one audited sanitizer
(CLAUDE.md §8, ``core/richtext.py``, ``Markdown.svelte``). Storing HTML instead would mean a
second render surface and a second XSS boundary for content written by strangers.

Written on stdlib ``html.parser`` rather than a dependency, like ``interactions/eml.py``: what
a mail body needs is not what a general converter does. Three rules are ours, not a library's.

**Every remote image is dropped to its alt text.** A tracking pixel is an image, and rendering
one would report back to the sender that the agency opened the mail. Only a ``cid:`` reference
survives — those bytes travel *inside* the message and become a stored file whose marker the
caller rewrites afterwards.

**Text is escaped.** A plain sentence containing ``*`` or ``_`` was never markdown and must not
become emphasis on the way through; the point is to preserve what the sender wrote, not to
reinterpret it.

**A table is a grid only when it looks like data.** Newsletter HTML is table-soup used for
layout, and rendering that as a grid of cells is worse than the flattened text it replaces —
so a table converts to GFM only on positive evidence (a header row, or a uniform grid of short
values) and otherwise flattens to lines, which loses arrangement but no words. A *nested*
table is layout by definition and never becomes a grid.

Pure functions, no I/O: the rules are unit-testable and nothing here reaches a database, a
network or a tenant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: Content we never want the words of, let alone the markup.
_DROP = frozenset({"script", "style", "head", "title", "meta", "link", "noscript"})

#: Anything that ends the current run of inline text.
_BLOCK = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "main", "aside",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre",
        "table", "thead", "tbody", "tfoot", "tr", "th", "td", "hr", "form", "fieldset",
        "figure", "figcaption", "address", "dl", "dt", "dd",
    }
)

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: Link schemes worth keeping. Anything else (``javascript:``, ``data:``, an unknown app
#: scheme) degrades to its own text — the sanitizer would strip it later anyway, and a link
#: that cannot be followed is noise in a stored body.
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:", "/", "#")

_MD_ESCAPE = re.compile(r"([\\`*_\[\]])")
#: A line that would otherwise read as a list item, heading, quote or table rule.
_LINE_LEAD = re.compile(r"^(\s*)([#>+|=-]|\d+[.)])")
_WS = re.compile(r"[^\S\n]+")
_BLANKS = re.compile(r"\n{3,}")
_CID_IMAGE = re.compile(r"!\[([^\]]*)\]\(cid:([^)]*)\)")

#: Beyond this a table cell is prose, not a value — the practical difference between a data
#: table and the nested layout tables newsletters are built from.
_MAX_DATA_CELL = 80


@dataclass
class _Block:
    text: str
    #: Prefix for continuation lines (quote markers + list indent).
    prefix: str = ""
    #: Prefix for the first line (the above, plus a list marker).
    lead: str = ""
    #: Join to the previous block with a single newline rather than a blank line.
    tight: bool = False
    #: Already markdown (a heading, a rule, a fenced block, a table) — rendered verbatim.
    #: Flowed text is not: its internal newlines are ``<br>``s and need a markdown hard break.
    raw: bool = False


@dataclass
class _Table:
    rows: list[list[str]] = field(default_factory=list)
    header: list[str] | None = None
    nested: bool = False


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._inline: list[str] = []
        self._drop = 0
        self._pre = 0
        self._quote = 0
        #: ["ul"/"ol", counter], outermost first.
        self._lists: list[list] = []
        #: A list item has opened and its marker has not been spent yet.
        self._pending_marker: str | None = None
        #: The next item starts a *top-level* list, so it needs a blank line above it. A
        #: nested list stays tight: a blank line there makes the whole list loose, and every
        #: item grows a paragraph's worth of spacing.
        self._list_start = False
        #: The hrefs of the ``<a>`` elements currently open (nesting is invalid but happens).
        self._hrefs: list[str] = []
        self._table: _Table | None = None
        self._table_depth = 0
        self._cell: list[str] | None = None
        self._row_is_header = False

    # --- assembly ----------------------------------------------------------- #
    def _prefix(self) -> str:
        return "> " * self._quote + "    " * max(0, len(self._lists) - 1)

    def _write(self, text: str) -> None:
        (self._cell if self._cell is not None else self._inline).append(text)

    def _flush(self, *, tight: bool = False) -> None:
        if self._cell is not None:
            # Inside a cell there are no blocks: a paragraph break is a space between words.
            self._cell.append(" ")
            return
        raw = "".join(self._inline)
        self._inline.clear()
        # `<br>` wrote a newline; collapse the runs of spaces around it without eating it.
        text = "\n".join(line.strip() for line in _WS.sub(" ", raw).split("\n")).strip()
        if text:
            self.blocks.append(self._block(text, tight=tight))

    def _emit(self, text: str, *, tight: bool = False) -> None:
        """A block whose text is already markdown (a heading, a rule, a fence, a table)."""
        self._flush()
        self.blocks.append(self._block(text, tight=tight, raw=True))

    def _block(self, text: str, *, tight: bool, raw: bool = False) -> _Block:
        prefix = self._prefix()
        lead = prefix
        if self._pending_marker is not None:
            lead = prefix + self._pending_marker
            self._pending_marker = None
            # An item joins the item above it — except the first of a top-level list, which
            # needs the blank line that separates it from whatever came before.
            tight = not self._list_start
            self._list_start = False
        return _Block(text=text, prefix=prefix, lead=lead, tight=tight, raw=raw)

    # --- parser callbacks --------------------------------------------------- #
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP:
            self._drop += 1
            return
        if self._drop:
            return
        attributes = {k.lower(): (v or "") for k, v in attrs}

        # Inline first: these are the only tags that mean anything inside a table cell.
        if tag == "br":
            # A newline here, a markdown hard break at render time — writing the two trailing
            # spaces now would only be eaten by the whitespace collapse in ``_flush``.
            self._write(" " if self._cell is not None else "\n")
            return
        if tag == "img":
            self._write(_image(attributes))
            return
        if tag == "a":
            self._hrefs.append(_href(attributes.get("href", "")))
            self._write("[")
            return
        if tag in ("strong", "b"):
            self._write("**")
            return
        if tag in ("em", "i"):
            self._write("*")
            return
        if tag in ("del", "s", "strike"):
            self._write("~~")
            return
        if tag == "code" and not self._pre:
            self._write("`")
            return

        if tag == "table":
            self._start_table()
            return
        if self._table is not None and tag in ("tr", "th", "td"):
            self._start_cell(tag)
            return
        if self._cell is not None:
            # Any other block tag inside a cell is layout: keep the words apart, nothing more.
            if tag in _BLOCK:
                self._write(" ")
            return

        if tag == "pre":
            self._flush()
            self._pre += 1
            return
        if tag == "blockquote":
            self._flush()
            self._quote += 1
            return
        if tag in ("ul", "ol"):
            self._flush()
            self._lists.append([tag, 0])
            self._list_start = len(self._lists) == 1
            return
        if tag == "li":
            self._flush()
            if not self._lists:
                # A stray <li>: a bullet is better than silently losing the item's shape.
                self._lists.append(["ul", 0])
            current = self._lists[-1]
            current[1] += 1
            self._pending_marker = "- " if current[0] == "ul" else f"{current[1]}. "
            return
        if tag == "hr":
            self._emit("---")
            return
        if tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP:
            self._drop = max(0, self._drop - 1)
            return
        if self._drop:
            return

        if tag == "a":
            self._close_link()
            return
        if tag in ("strong", "b"):
            self._write("**")
            return
        if tag in ("em", "i"):
            self._write("*")
            return
        if tag in ("del", "s", "strike"):
            self._write("~~")
            return
        if tag == "code" and not self._pre:
            self._write("`")
            return

        if tag == "table":
            self._end_table()
            return
        if self._table is not None and tag in ("tr", "th", "td"):
            self._end_cell(tag)
            return
        if self._cell is not None:
            if tag in _BLOCK:
                self._write(" ")
            return

        if tag == "pre" and self._pre:
            text = "".join(self._inline).strip("\n")
            self._inline.clear()
            self._pre -= 1
            if text.strip():
                self._emit(f"```\n{text}\n```")
            return
        if tag == "blockquote":
            self._flush()
            self._quote = max(0, self._quote - 1)
            return
        if tag in ("ul", "ol"):
            self._flush()
            if self._lists:
                self._lists.pop()
            self._pending_marker = None
            return
        if tag == "li":
            self._flush()
            # An empty <li> would otherwise carry its marker onto whatever comes next.
            self._pending_marker = None
            return
        if tag in _HEADINGS:
            text = _WS.sub(" ", "".join(self._inline)).strip()
            self._inline.clear()
            if text:
                # Never `#`/`##`: the app's renderer draws h3 and below, so a top-level
                # heading spelled faithfully would be dropped — content lost to fidelity.
                self._emit(f"{'#' * max(3, int(tag[1]))} {text}")
            return
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._drop:
            return
        if self._pre:
            self._inline.append(data)
            return
        self._write(escape_markdown(data))

    def flush_tail(self) -> None:
        """Text after the last close tag — a body that never closed its final ``<div>``."""
        self._cell = None
        self._table = None
        self._flush()

    # --- links -------------------------------------------------------------- #
    def _close_link(self) -> None:
        """Turn the buffered ``[label`` into a link, or unwrap it when there is no href."""
        href = self._hrefs.pop() if self._hrefs else ""
        target = self._cell if self._cell is not None else self._inline
        for index in range(len(target) - 1, -1, -1):
            if target[index] == "[":
                label = "".join(target[index + 1 :]).strip()
                del target[index:]
                if label and href:
                    target.append(f"[{label}]({href})")
                elif label:
                    target.append(label)
                return

    # --- tables ------------------------------------------------------------- #
    def _start_table(self) -> None:
        self._table_depth += 1
        if self._table is None:
            self._flush()
            self._table = _Table()
        else:
            # Nested: layout by definition. Its cells go on being cell text of the outer one.
            self._table.nested = True

    def _end_table(self) -> None:
        self._table_depth = max(0, self._table_depth - 1)
        if self._table_depth or self._table is None:
            return
        table, self._table = self._table, None
        self._cell = None
        self._render_table(table)

    def _start_cell(self, tag: str) -> None:
        assert self._table is not None
        if self._table_depth > 1:
            # A nested table's structure is not structure; keep the words apart.
            self._write(" ")
            return
        if tag == "tr":
            self._row_is_header = False
            self._table.rows.append([])
            return
        self._row_is_header = self._row_is_header or tag == "th"
        if not self._table.rows:
            self._table.rows.append([])
        self._cell = []

    def _end_cell(self, tag: str) -> None:
        assert self._table is not None
        if self._table_depth > 1:
            self._write(" ")
            return
        if tag in ("th", "td") and self._cell is not None:
            self._table.rows[-1].append(_WS.sub(" ", "".join(self._cell)).strip())
            self._cell = None
            return
        if tag == "tr":
            if self._row_is_header and self._table.header is None and self._table.rows:
                self._table.header = self._table.rows.pop()
            self._row_is_header = False

    def _render_table(self, table: _Table) -> None:
        rows = [row for row in table.rows if any(cell for cell in row)]
        if not rows and not table.header:
            return
        if _is_data_table(table, rows):
            head = table.header if table.header is not None else rows.pop(0)
            columns = max([len(head)] + [len(row) for row in rows])
            lines = [
                "| " + " | ".join(_cell(c) for c in _pad(head, columns)) + " |",
                "| " + " | ".join("---" for _ in range(columns)) + " |",
            ]
            lines += [
                "| " + " | ".join(_cell(c) for c in _pad(row, columns)) + " |" for row in rows
            ]
            self._emit("\n".join(lines))
            return
        # Layout table: keep the words, drop the arrangement. One line per row.
        self._flush()
        for row in ([table.header] if table.header else []) + rows:
            text = " ".join(cell for cell in row if cell).strip()
            if text:
                self._emit(text)


def _pad(row: list[str], columns: int) -> list[str]:
    return list(row) + [""] * (columns - len(row))


def _is_data_table(table: _Table, rows: list[list[str]]) -> bool:
    """Positive evidence only: a header row, or a uniform grid of short values."""
    if table.nested:
        return False
    if table.header:
        return True
    if len(rows) < 2:
        return False
    widths = {len(row) for row in rows}
    if len(widths) != 1 or widths.pop() < 2:
        return False
    return all(len(cell) <= _MAX_DATA_CELL for row in rows for cell in row)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip() or " "


def _href(raw: str) -> str:
    """Keep a link only if it can be followed; anything else unwraps to its own text."""
    href = raw.strip()
    return href if href.lower().startswith(_SAFE_SCHEMES) else ""


def _image(attributes: dict[str, str]) -> str:
    """``cid:`` images survive as a marker; everything remote degrades to its alt text."""
    src = (attributes.get("src") or "").strip()
    alt = escape_markdown((attributes.get("alt") or "").strip())
    if src.lower().startswith("cid:"):
        return f"![{alt}](cid:{src[4:]})"
    return alt


def escape_markdown(text: str) -> str:
    """Escape what would otherwise be read as markup. Received text is not our markdown."""
    escaped = _MD_ESCAPE.sub(r"\\\1", text)
    return "\n".join(_LINE_LEAD.sub(r"\1\\\2", line) for line in escaped.split("\n"))


def _render(blocks: list[_Block]) -> str:
    out: list[str] = []
    for index, block in enumerate(blocks):
        lines = block.text.split("\n")
        if not block.raw:
            # Every internal newline came from a `<br>`, and markdown spells that as two
            # trailing spaces on the line before it.
            lines = [line + "  " for line in lines[:-1]] + lines[-1:]
        if index and not block.tight:
            out.append("")
        out.extend(
            (block.lead if i == 0 else block.prefix) + line for i, line in enumerate(lines)
        )
    return _BLANKS.sub("\n\n", "\n".join(out)).strip()


def html_to_markdown(html: str | None) -> str | None:
    """Convert an HTML body to markdown source. ``None``/blank in, ``None`` out.

    Never raises: a malformed message must still land on the timeline. A tree the parser
    cannot make sense of degrades to whatever text it did see, which is no worse than the
    tag-stripping this replaces.
    """
    if not html or not html.strip():
        return None
    converter = _Converter()
    try:
        converter.feed(html)
        converter.close()
    except Exception:  # noqa: BLE001 — one unparseable body must not lose the message
        pass
    converter.flush_tail()
    return _render(converter.blocks) or None


def rewrite_cid_images(markdown: str | None, resolved: dict[str, str]) -> str | None:
    """Point ``![alt](cid:x)`` at the files those parts were stored as.

    ``resolved`` maps a Content-ID to a file id. What lands in the body is
    ``![alt](file:<uuid>)`` — a **stored marker, not a URL**, the same shape as ``mention:``
    and ``crm://`` (docs/UX.md): the web resolves it at render time, so no API path is frozen
    into a stored body and a consumer that draws no images (the document renderer) ignores it.

    A cid the message referenced but we did not store — an oversized part, a disallowed type,
    a broken reference — degrades to its alt text rather than leaving a dead marker.
    """
    if not markdown:
        return markdown

    def replace(match: re.Match[str]) -> str:
        alt, cid = match.group(1), match.group(2).strip()
        file_id = resolved.get(cid) or resolved.get(cid.strip("<>"))
        return f"![{alt}](file:{file_id})" if file_id else alt

    return _CID_IMAGE.sub(replace, markdown)


def referenced_cids(markdown: str | None) -> set[str]:
    """The Content-IDs a converted body points at — what the caller must resolve."""
    return {match.group(2).strip() for match in _CID_IMAGE.finditer(markdown or "")}

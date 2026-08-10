"""Charts for a printed document: inline SVG, drawn server-side, no network.

Why SVG and not an image service: ``engine.no_network_fetcher`` answers ``data:`` and raises
on everything else, deliberately — so a chart cannot be an ``<img src="https://…">``. That
constraint turns out to be the right design anyway. The n8n workflow this replaces built
QuickChart URLs (``http://quickchart:3400/chart?c=…``), which meant a second container, a
three-second render wait, a raster image in a vector document, and the client's own numbers
travelling in a URL. Inline SVG is none of those things: it prints at the printer's
resolution, costs one string concatenation, and never leaves the process.

**Everything is passed in.** No function here owns a hex (Golden Rule 4) — the accent is the
tenant's, resolved by ``colors.accent_for`` upstream. Number formatting arrives as a callable
because a document formats in *its own* locale, not the viewer's (docs/INVOICING.md).

Form choices are deliberate and two of them differ from the workflow being replaced:

* **One series → one colour, never a hue per bar.** Colouring "sessions per channel" with a
  fixed green/blue/orange/red map double-encodes bar length as hue and burns the only free
  channel on information the chart already shows. The bars are the tenant's accent; the
  channel is named on the axis.
* **Part-to-whole is a share bar, not a doughnut.** A doughnut where Google holds 95 % of the
  slices is a circle with one slice; the reader learns nothing a sentence would not tell them
  faster. A single 100 %-wide stacked bar states the same split in a quarter of the height and
  survives a black-and-white printer.

Print-specific: gridlines are solid hairlines (a dashed grid reads as a threshold), text wears
ink/muted tokens rather than the series colour, and no value is stamped on a mark — every
chart in a report sits beside the table that carries its numbers, which is the table-view
requirement met by construction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

from app.core.documents.colors import INK, MUTED, RGB, RULE, mix_on_white, rgb_hex

#: Bar thickness cap — a bar that fills its band reads as a block, not a mark.
_MAX_BAR = 24.0
#: The surface gap that separates touching marks. White does the separating, never a stroke.
_GAP = 2.0
#: Rounded data-end; square at the baseline.
_RADIUS = 4.0
#: Rough advance width of the document face at 1pt, for deciding whether a label fits.
_CHAR_W = 0.55

Formatter = Callable[[float], str]


def _default_format(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", ".")


@dataclass(frozen=True)
class ChartStyle:
    """The palette a chart draws in — every value resolved by the caller.

    ``accent`` is the tenant's contrast-corrected brand colour; ``comparison`` is the
    de-emphasis grey a previous period wears. They differ in *lightness* as well as hue so the
    pair still separates on a black-and-white printer, which is where a client report often
    ends up.
    """

    accent: str
    comparison: str = rgb_hex(mix_on_white(INK, 0.34))
    ink: str = rgb_hex(INK)
    muted: str = rgb_hex(MUTED)
    rule: str = rgb_hex(RULE)
    font_size: float = 8.0


def _esc(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _fits(text: str, width: float, font_size: float) -> bool:
    return len(text) * _CHAR_W * font_size + 8 <= width


def _nice_ceiling(value: float) -> tuple[float, list[float]]:
    """A clean axis top and its ticks — 0 / 1.000 / 2.000, never 0 / 837 / 1.674."""
    if value <= 0:
        return 1.0, [0.0, 1.0]
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        top = step * magnitude
        if top >= value:
            divisions = 4 if step in (1, 2, 10) else 5
            return top, [top * i / divisions for i in range(divisions + 1)]
    return value, [0.0, value]


def _column_path(x: float, y: float, width: float, height: float) -> str:
    """A column with a rounded cap and a square foot."""
    if height <= 0:
        return ""
    radius = min(_RADIUS, width / 2, height)
    bottom = y + height
    return (
        f"M{x:.2f},{bottom:.2f} V{y + radius:.2f} "
        f"Q{x:.2f},{y:.2f} {x + radius:.2f},{y:.2f} "
        f"H{x + width - radius:.2f} Q{x + width:.2f},{y:.2f} "
        f"{x + width:.2f},{y + radius:.2f} V{bottom:.2f} Z"
    )


def _svg(width: float, height: float, body: str, title: str) -> str:
    """A standalone, responsive, accessible SVG fragment.

    ``font-family: inherit`` so the chart wears the document's face rather than carrying its
    own; ``role="img"`` + ``<title>`` so a screen reader on the HTML preview announces what it
    is instead of skipping an anonymous graphic.

    **The width is a CSS declaration, not only a presentation attribute — and that is the whole
    bug.** ``width="100%"`` on the element is the responsive-SVG idiom every browser honours;
    WeasyPrint does not resolve a percentage there, so with no CSS ``width`` and ``height:
    auto`` the box laid out at 0×0. Every chart this module ever drew was visible in the
    preview and *absent from the printed PDF* — the one property the shared renderer exists to
    guarantee, broken by four characters of stylesheet.

    ``width: 100%`` in the style block is the fix. The ``width``/``height`` attributes in user
    units come along as the intrinsic size, which is what a strict engine scales the viewBox
    ratio from — but they are not sufficient on their own: with no CSS width, WeasyPrint prints
    the 320×190 chart 320×320. State the used width, and the ratio follows.

    The lesson generalises past this one tag: a preview and a print that share HTML still do not
    share a layout engine, so "the markup is in the document" is not evidence that anything was
    drawn. ``tests/test_reporting.py`` asserts the chart has *area on a rendered page*, and
    fails on the markup this replaced.
    """
    return (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'style="font-family:inherit;display:block;width:100%;max-width:100%;height:auto">'
        f"<title>{_esc(title)}</title>{body}</svg>"
    )


def _axis(
    style: ChartStyle,
    plot_left: float,
    plot_right: float,
    plot_top: float,
    plot_bottom: float,
    ticks: Sequence[float],
    top: float,
    fmt: Formatter,
) -> str:
    """Solid hairline gridlines with clean, right-aligned, tabular ticks."""
    out: list[str] = []
    height = plot_bottom - plot_top
    for tick in ticks:
        y = plot_bottom - (tick / top) * height if top else plot_bottom
        out.append(
            f'<line x1="{plot_left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}" '
            f'stroke="{style.rule}" stroke-width="1" />'
        )
        out.append(
            f'<text x="{plot_left - 6:.1f}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="{style.font_size:.1f}" fill="{style.muted}" '
            f'style="font-variant-numeric:tabular-nums">{_esc(fmt(tick))}</text>'
        )
    return "".join(out)


def _ellipsized(text: str, width: float, font_size: float) -> str:
    """``Organic Social`` in the room for ``Organic S…`` — cut, never overlapped.

    Rotation buys a category label more room; it does not buy unlimited room, and a name that
    still does not fit has to give way to its neighbour somehow. An ellipsis says "there was
    more here"; two names printed through each other say nothing and look broken.
    """
    room = int((width - 8) / max(_CHAR_W * font_size, 0.01))
    if room < 2 or len(text) <= room:
        return text
    return text[: room - 1].rstrip() + "…"


#: Room under the category strip for the series legend.
_LEGEND_STRIP = 16.0
#: The plot never shrinks below this, however deep the labels run — a 20pt-tall bar chart
#: carries no information, so a long category name grows the canvas instead of eating the data.
_MIN_PLOT = 70.0


def _fit_baseline(height: float, label_depth: float, legend: float) -> tuple[float, float]:
    """``(height, baseline)`` — the canvas the labels actually need, and where the axis sits."""
    baseline = height - label_depth - legend
    if baseline < _MIN_PLOT:
        height += _MIN_PLOT - baseline
        baseline = _MIN_PLOT
    return height, baseline


def _rotates(style: ChartStyle, labels: Sequence[str], band: float) -> bool:
    return any(not _fits(label, band, style.font_size) for label in labels)


def _label_depth(style: ChartStyle, labels: Sequence[str], band: float) -> float:
    """How much vertical room the category strip needs — measured, not assumed.

    The strip used to be a constant (34pt, 46pt with a legend) chosen for horizontal labels,
    and a rotated name simply ran out the bottom of it and through the legend. A label's depth
    is a function of its own length and angle, so the geometry asks it rather than guessing:
    that is what lets one chart carry ``Direct`` and the next ``Organic Social`` without either
    of them being laid out for the other.
    """
    if not _rotates(style, labels, band):
        return 15.0
    budget = band * 1.41
    longest = max(
        (len(_ellipsized(label, budget, style.font_size)) for label in labels), default=0
    )
    # sin 45° of the rotated advance, plus the gap between baseline and the first glyph.
    return 10.0 + longest * _CHAR_W * style.font_size * 0.707


def _category_labels(
    style: ChartStyle, labels: Sequence[str], centres: Sequence[float], baseline: float
) -> str:
    """Category names under the baseline, rotated only when they would otherwise collide."""
    out: list[str] = []
    band = (centres[1] - centres[0]) if len(centres) > 1 else 999.0
    rotate = _rotates(style, labels, band)
    # A rotated label is not a free label: anchored at its end and laid at -45°, it still eats
    # `length × cos 45°` of horizontal room, so neighbours cross once a name runs past ~1.4
    # bands. That is the whole reason the printed chart showed "Organic Social" driven through
    # "Referral" — the old code chose to rotate and then never asked whether rotating had been
    # enough.
    budget = band * 1.41
    for label, centre in zip(labels, centres, strict=False):
        if rotate:
            out.append(
                f'<text transform="translate({centre:.1f},{baseline + 8:.1f}) rotate(-45)" '
                f'text-anchor="end" font-size="{style.font_size:.1f}" '
                f'fill="{style.muted}">'
                f"{_esc(_ellipsized(label, budget, style.font_size))}</text>"
            )
        else:
            out.append(
                f'<text x="{centre:.1f}" y="{baseline + 11:.1f}" text-anchor="middle" '
                f'font-size="{style.font_size:.1f}" fill="{style.muted}">{_esc(label)}</text>'
            )
    return "".join(out)


#: One legend row's height, key included.
_LEGEND_LINE = 14.0


def _legend_span(style: ChartStyle, label: str) -> float:
    return 12 + len(label) * _CHAR_W * style.font_size + 18


def _legend_rows(
    style: ChartStyle, entries: Sequence[tuple[str, str]], width: float
) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = [[]]
    cursor = 0.0
    for entry in entries:
        span = _legend_span(style, entry[1])
        if rows[-1] and cursor + span > width:
            rows.append([])
            cursor = 0.0
        rows[-1].append(entry)
        cursor += span
    return rows


def _legend_depth(
    style: ChartStyle, entries: Sequence[tuple[str, str]], width: float
) -> float:
    """The room these keys need. Measured, so a caller can buy it before drawing."""
    return _LEGEND_LINE * len(_legend_rows(style, entries, width))


def _legend(
    style: ChartStyle,
    entries: Sequence[tuple[str, str]],
    x: float,
    y: float,
    width: float = 1e9,
) -> str:
    """A legend is always present for two or more series — identity is never colour alone.

    **And it wraps.** Laid out on one line without ever asking how wide the canvas was, a share
    bar with six named segments ran its last two keys clean off the right-hand edge of the
    page: the reader was shown four colours out of six, with nothing to say the others existed.
    Rows are measured the same way the entries are drawn, so the wrap lands where the glyphs
    actually reach it.
    """
    out: list[str] = []
    for index, row in enumerate(_legend_rows(style, entries, width)):
        cursor = x
        line = y + index * _LEGEND_LINE
        for colour, label in row:
            out.append(
                f'<rect x="{cursor:.1f}" y="{line - 6:.1f}" width="8" height="8" rx="2" '
                f'fill="{colour}" />'
            )
            out.append(
                f'<text x="{cursor + 12:.1f}" y="{line + 1:.1f}" '
                f'font-size="{style.font_size:.1f}" '
                f'fill="{style.muted}">{_esc(label)}</text>'
            )
            cursor += _legend_span(style, label)
    return "".join(out)


#: A category name longer than this is cut wherever it is drawn — at some point a label stops
#: being a label. It is generous, because the horizontal form has room for real names.
_MAX_ROW_LABEL = 30
#: One category's band in the horizontal form: two bars, their gap, and air around the pair.
_ROW_BAND = 21.0
#: How much of a horizontal chart the names may take before the bars stop being comparable.
_NAME_COLUMN = 0.36
#: The horizontal form's own canvas, wider than the column form's.
#:
#: A chart is scaled by its container (`standard.css`: 150 mm), so the viewBox width is really a
#: *type size* control — 320 user units across 150 mm draws 8-unit text at about 10.5 pt, half
#: again the size of the table underneath it, and makes ten rows fill a third of the sheet. At
#: 440 the same text lands near the document's own 8 pt and the chart takes the room it is
#: worth. Nothing about the drawing changes; only how much paper it is stretched over.
_ROWS_WIDTH = 440.0


def _truncates(style: ChartStyle, labels: Sequence[str], band: float) -> bool:
    """Would the vertical form have to cut a name to fit it under a bar?

    Rotation buys room and then runs out of it, and what the reader gets when it does is
    ``Paid…`` twice over — two different channels printed identically, on a chart whose whole
    job is telling them apart. That is the signal to change *form* rather than to keep
    shortening: a horizontal bar chart writes every name out in full, and the only thing it
    costs is a shape the reader is equally used to.
    """
    if not _rotates(style, labels, band):
        return False
    budget = band * 1.41
    return any(_ellipsized(label, budget, style.font_size) != label for label in labels)


def _row_label(text: str) -> str:
    return text if len(text) <= _MAX_ROW_LABEL else text[: _MAX_ROW_LABEL - 1].rstrip() + "…"


def _bars(
    labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    *,
    style: ChartStyle,
    title: str,
    fmt: Formatter,
    width: float = _ROWS_WIDTH,
) -> str:
    """Categories down the side, bars across — the form long names belong in.

    Everything the column form decides about ink is kept: one colour per series, the comparison
    in its de-emphasised grey, gridlines as solid hairlines, and no value stamped on a mark
    (the table below carries every number). Only the axis the names sit on has changed, and
    with it the ceiling on how long a name may be.
    """
    columns = [[max(0.0, float(v or 0)) for v in values] for _, values in series]
    top, ticks = _nice_ceiling(max((max(c, default=0.0) for c in columns), default=0.0))
    names = [_row_label(label) for label in labels]
    left = min(width * _NAME_COLUMN, max(len(n) for n in names) * _CHAR_W * style.font_size + 10)
    right = width - 24.0
    plot_top = 16.0
    band = _ROW_BAND if len(series) > 1 else _ROW_BAND * 0.8
    baseline = plot_top + band * len(labels)
    colours = [style.accent, style.comparison][: len(series)]
    legend_entries = [(colours[i], name) for i, (name, _) in enumerate(series)]
    legend = _legend_depth(style, legend_entries, width) if len(series) > 1 else 0.0
    height = baseline + 18.0 + legend

    body: list[str] = []
    for tick in ticks:
        x = left + (tick / top) * (right - left) if top else left
        body.append(
            f'<line x1="{x:.1f}" y1="{plot_top:.1f}" x2="{x:.1f}" y2="{baseline:.1f}" '
            f'stroke="{style.rule}" stroke-width="1" />'
        )
        body.append(
            f'<text x="{x:.1f}" y="{plot_top - 5:.1f}" text-anchor="middle" '
            f'font-size="{style.font_size:.1f}" fill="{style.muted}" '
            f'style="font-variant-numeric:tabular-nums">{_esc(fmt(tick))}</text>'
        )
    bar = min(_MAX_BAR * 0.5, (band - _GAP * 3) / len(series))
    for index, name in enumerate(names):
        centre = plot_top + band * (index + 0.5)
        body.append(
            f'<text x="{left - 7:.1f}" y="{centre + 3:.1f}" text-anchor="end" '
            f'font-size="{style.font_size:.1f}" fill="{style.muted}">{_esc(name)}</text>'
        )
        group = bar * len(series) + _GAP * (len(series) - 1)
        for slot, values in enumerate(columns):
            value = values[index] if index < len(values) else 0.0
            length = (value / top) * (right - left) if top else 0.0
            y = centre - group / 2 + slot * (bar + _GAP)
            path = _row_path(left, y, length, bar)
            if path:
                body.append(f'<path d="{path}" fill="{colours[slot]}" />')
    body.append(
        f'<line x1="{left:.1f}" y1="{plot_top:.1f}" x2="{left:.1f}" y2="{baseline:.1f}" '
        f'stroke="{style.rule}" stroke-width="1" />'
    )
    if len(series) > 1:
        body.append(_legend(style, legend_entries, left, baseline + 14.0, width - left))
    return _svg(width, height, "".join(body), title)


def _row_path(x: float, y: float, length: float, thickness: float) -> str:
    """A bar with a rounded end and a square foot — the column path, laid on its side."""
    if length <= 0:
        return ""
    radius = min(_RADIUS, thickness / 2, length)
    end = x + length
    return (
        f"M{x:.2f},{y:.2f} H{end - radius:.2f} "
        f"Q{end:.2f},{y:.2f} {end:.2f},{y + radius:.2f} "
        f"V{y + thickness - radius:.2f} Q{end:.2f},{y + thickness:.2f} "
        f"{end - radius:.2f},{y + thickness:.2f} H{x:.2f} Z"
    )


def column_chart(
    labels: Sequence[str],
    values: Sequence[float],
    *,
    style: ChartStyle,
    title: str = "",
    fmt: Formatter = _default_format,
    width: float = 320.0,
    height: float = 170.0,
) -> str:
    """One series, one colour. "Compare magnitude" — the bar length is the whole message."""
    labels, values = list(labels)[:12], [max(0.0, float(v or 0)) for v in list(values)[:12]]
    if not labels:
        return ""
    left, right, plot_top = 44.0, width - 6.0, 8.0
    if _truncates(style, labels, (right - left) / len(labels)):
        return _bars(labels, [("", values)], style=style, title=title, fmt=fmt)
    top, ticks = _nice_ceiling(max(values, default=0.0))
    band = (right - left) / len(labels)
    height, baseline = _fit_baseline(height, _label_depth(style, labels, band), 0.0)
    bar = min(_MAX_BAR, band - _GAP * 2)
    body = [_axis(style, left, right, plot_top, baseline, ticks, top, fmt)]
    centres: list[float] = []
    for index, value in enumerate(values):
        centre = left + band * (index + 0.5)
        centres.append(centre)
        bar_height = (value / top) * (baseline - plot_top) if top else 0.0
        path = _column_path(centre - bar / 2, baseline - bar_height, bar, bar_height)
        if path:
            body.append(f'<path d="{path}" fill="{style.accent}" />')
    body.append(
        f'<line x1="{left:.1f}" y1="{baseline:.1f}" x2="{right:.1f}" y2="{baseline:.1f}" '
        f'stroke="{style.rule}" stroke-width="1" />'
    )
    body.append(_category_labels(style, labels, centres, baseline))
    return _svg(width, height, "".join(body), title)


def grouped_columns(
    labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    *,
    style: ChartStyle,
    title: str = "",
    fmt: Formatter = _default_format,
    width: float = 320.0,
    height: float = 190.0,
) -> str:
    """Two series side by side — this period against the one it is compared with.

    Capped at two: a third grouped column per category is where a print chart stops being
    readable, and the report never has one (a period has exactly one comparison).
    """
    labels = list(labels)[:10]
    series = list(series)[:2]
    if not labels or not series:
        return ""
    columns = [[max(0.0, float(v or 0)) for v in list(vals)[:10]] for _, vals in series]
    left, right, plot_top = 44.0, width - 6.0, 8.0
    if _truncates(style, labels, (right - left) / len(labels)):
        return _bars(
            labels,
            [(name, columns[i]) for i, (name, _) in enumerate(series)],
            style=style,
            title=title,
            fmt=fmt,
        )
    top, ticks = _nice_ceiling(max((max(c, default=0.0) for c in columns), default=0.0))
    band = (right - left) / len(labels)
    legend_entries = [
        ([style.accent, style.comparison][i], name) for i, (name, _) in enumerate(series)
    ]
    height, baseline = _fit_baseline(
        height,
        _label_depth(style, labels, band),
        _legend_depth(style, legend_entries, right - left),
    )
    colours = [style.accent, style.comparison][: len(series)]
    bar = min(_MAX_BAR, (band - _GAP * 3) / len(series))
    body = [_axis(style, left, right, plot_top, baseline, ticks, top, fmt)]
    centres: list[float] = []
    group_width = bar * len(series) + _GAP * (len(series) - 1)
    for index in range(len(labels)):
        centre = left + band * (index + 0.5)
        centres.append(centre)
        for slot, values in enumerate(columns):
            value = values[index] if index < len(values) else 0.0
            bar_height = (value / top) * (baseline - plot_top) if top else 0.0
            x = centre - group_width / 2 + slot * (bar + _GAP)
            path = _column_path(x, baseline - bar_height, bar, bar_height)
            if path:
                body.append(f'<path d="{path}" fill="{colours[slot]}" />')
    body.append(
        f'<line x1="{left:.1f}" y1="{baseline:.1f}" x2="{right:.1f}" y2="{baseline:.1f}" '
        f'stroke="{style.rule}" stroke-width="1" />'
    )
    body.append(_category_labels(style, labels, centres, baseline))
    body.append(
        _legend(
            style,
            legend_entries,
            left,
            height - _legend_depth(style, legend_entries, right - left) + 8.0,
            right - left,
        )
    )
    return _svg(width, height, "".join(body), title)


@dataclass(frozen=True)
class ShareSegment:
    """One share of a whole: its label, its value, its fraction, and the colour it is drawn in.

    ``tail`` marks the folded remainder — the segment that stands for every row past
    ``max_segments`` rather than for a row of its own.
    """

    label: str
    value: float
    fraction: float
    colour: str
    #: How much accent the tint carries (1.0 = the accent itself, 0 = white). Kept beside the
    #: hex because "is this fill dark enough for white text" is answered from the recipe, not
    #: re-derived from the colour it produced.
    weight: float
    tail: bool = False


def share_palette(
    items: Sequence[tuple[str, float]],
    *,
    style: ChartStyle,
    other_label: str = "",
    max_segments: int = 5,
) -> list[ShareSegment]:
    """The share bar's own segment assignment, as data.

    Split out of :func:`share_bar` so the *table* under a chart can mark each row with the
    colour of its segment. A legend that names six colours the table beside it does not repeat
    leaves the reader matching two orderings by eye — which is most of what a share chart was
    supposed to save them. One function answering both is what makes the dot and the segment
    agree by construction; two call sites choosing the same formula is how they stop agreeing
    the first time one of them is tuned.

    Segments are tints of the *tenant's* accent, light → dark by share, so it stays one hue
    (a rainbow of six brand-unrelated colours for "which search engine" says nothing the
    order does not) and it degrades to distinguishable greys in black and white. The tail
    folds into one segment rather than growing an eighth colour nobody can name — and the
    fold is part of the *scale*, so a caller that wants colours matching a drawn bar has to
    pass the same ``other_label`` the bar was drawn with.
    """
    ranked = sorted(
        ((str(label), max(0.0, float(value or 0))) for label, value in items),
        key=lambda pair: pair[1],
        reverse=True,
    )
    total = sum(value for _, value in ranked)
    if total <= 0:
        return []
    head: list[tuple[str, float, bool]] = [
        (label, value, False) for label, value in ranked[:max_segments]
    ]
    tail = sum(value for _, value in ranked[max_segments:])
    if tail > 0 and other_label:
        head.append((other_label, tail, True))
    steps = max(len(head) - 1, 1)
    out: list[ShareSegment] = []
    for index, (label, value, is_tail) in enumerate(head):
        weight = 1.0 - 0.62 * (index / steps)
        out.append(
            ShareSegment(
                label=label,
                value=value,
                fraction=value / total,
                colour=rgb_hex(mix_on_white(_hex_rgb(style.accent), weight)),
                weight=weight,
                tail=is_tail,
            )
        )
    return out


def share_bar(
    items: Sequence[tuple[str, float]],
    *,
    style: ChartStyle,
    title: str = "",
    other_label: str = "",
    width: float = 320.0,
    height: float = 62.0,
    max_segments: int = 5,
) -> str:
    """Part-to-whole as one 100 %-wide stacked bar, with a legend under it."""
    segments = share_palette(
        items, style=style, other_label=other_label, max_segments=max_segments
    )
    if not segments:
        return ""
    left, right = 4.0, width - 4.0
    bar_top, bar_height = 8.0, 18.0
    span = right - left
    body: list[str] = []
    legend: list[tuple[str, str]] = []
    cursor = left
    for index, share in enumerate(segments):
        # The trailing 2px gap comes out of each segment, so the bar still ends flush right.
        last = index == len(segments) - 1
        segment = max(0.0, span * share.fraction - (0.0 if last else _GAP))
        body.append(
            f'<rect x="{cursor:.2f}" y="{bar_top:.1f}" width="{segment:.2f}" '
            f'height="{bar_height:.1f}" rx="2" fill="{share.colour}" />'
        )
        # A share that is not zero must not print as "0%": the folded tail below one per cent
        # is still two sessions, and a legend reading "Overig 0%" invites the reader to wonder
        # what it is doing there. The test is what the *rounding* produces, not a threshold
        # guessed at beside it — exactly 0,5 % rounds to "0%" under banker's rounding, so a
        # `< 0.005` guard lets through the one value it was written to catch.
        percent = f"{share.fraction * 100:.0f}%"
        if share.fraction > 0 and percent == "0%":
            percent = "<1%"
        if _fits(percent, segment, style.font_size):
            # Inside a colored fill is the one place a label may not wear an ink token: pick
            # white or ink by the fill's own luminance so it always clears contrast.
            fill = "#ffffff" if share.weight > 0.55 else style.ink
            body.append(
                f'<text x="{cursor + segment / 2:.2f}" y="{bar_top + 12.5:.1f}" '
                f'text-anchor="middle" font-size="{style.font_size:.1f}" fill="{fill}" '
                f'style="font-variant-numeric:tabular-nums">{percent}</text>'
            )
        legend.append((share.colour, f"{share.label} {percent}"))
        cursor += segment + _GAP
    # The canvas grows to whatever the keys need. Six named segments do not fit on one line at
    # this width, and the alternative to growing is the one that shipped: two of them drawn
    # past the right-hand edge of the paper.
    height = bar_top + bar_height + 10.0 + _legend_depth(style, legend, span)
    body.append(_legend(style, legend, left, bar_top + bar_height + 18.0, span))
    return _svg(width, height, "".join(body), title)


def sparkline(
    values: Sequence[float],
    *,
    style: ChartStyle,
    title: str = "",
    width: float = 96.0,
    height: float = 22.0,
) -> str:
    """A 2px trend line with an end marker — the stat-tile companion, not a chart of its own."""
    points = [float(v or 0) for v in values][-40:]
    if len(points) < 2:
        return ""
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    step = (width - 6) / (len(points) - 1)

    def y_of(value: float) -> float:
        return height - 4 - ((value - low) / span) * (height - 9)

    path = " ".join(
        f"{'M' if i == 0 else 'L'}{3 + i * step:.2f},{y_of(v):.2f}"
        for i, v in enumerate(points)
    )
    end_x, end_y = 3 + (len(points) - 1) * step, y_of(points[-1])
    return _svg(
        width,
        height,
        f'<path d="{path}" fill="none" stroke="{style.accent}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" />'
        f'<circle cx="{end_x:.2f}" cy="{end_y:.2f}" r="2.5" fill="{style.accent}" '
        f'stroke="#ffffff" stroke-width="2" />',
        title,
    )


def _hex_rgb(value: str) -> RGB:
    from app.core.documents.colors import hex_rgb

    return hex_rgb(value, INK)

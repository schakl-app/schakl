"""The snapshot, in the words the document prints (issue #300).

`docs/REPORTING.md` claims that "the prose and the tables describe the same figures *by
construction*, rather than both re-querying and hoping". That was true of the *figures* and
false of everything around them, because the model was handed the raw snapshot: Google's field
names and Python's floats. What came back was a Dutch paragraph reading

    "in juli 2026 waren er 4124 sessies en 3781 totalUsers, met 2810 newUsers en 879 keyEvents
     … De engagementRate was 0.4595 … een userEngagementDuration van 37570.0 seconden …
     (compare_sessions 61, delta 21.3)"

Every one of those defects is the same defect. The model was quoting its input faithfully, and
its input was a database row. A house-style instruction would not have fixed it either: no tone
can teach a model that `keyEvents` is spelled *belangrijke gebeurtenissen* in this tenant's
catalogue, and asking it to reformat `0.4595` is asking it to do arithmetic — the one thing
`prompts._GROUNDING` forbids, for good reasons that have not changed.

So the document is **presented** instead. Every key is the label the table prints, every value
is the string the table prints, resolved through the very functions the renderer uses
(`render/context.fmt_metric`, `fmt_delta`, `metric_label`). The model cannot write `totalUsers`
because the word is not in front of it, and it cannot round `0.4595` wrongly because what it
was given is `46,0%`. The two surfaces now agree because there is one formatter, which is the
same argument that made the renderer shared in the first place.

Three properties are worth stating, because each is load-bearing:

**Nothing is invented and nothing is dropped silently.** A capped table says how many rows it
is showing out of how many, in the data, so a model describing "the referrers" is describing a
set it has been told the size of.

**It is smaller than what it replaces**, which matters against `MAX_INPUT_CHARS`: a formatted
string is shorter than a float, and the compare/delta dicts stop being repeated per section.

**It is not the snapshot.** `data_snapshot` still holds the raw numbers — that is what makes a
report a record, and re-rendering it next December must not depend on a locale decision taken
today.
"""

from __future__ import annotations

from typing import Any

from app.i18n import translate
from app.modules.reporting.render.context import (
    always_zero,
    channel_label,
    fmt_delta,
    fmt_metric,
    metric_label,
    ordered_metrics,
    shape_section,
)

#: How many rows of one table the model reads. It is writing a paragraph about the shape of a
#: table, not transcribing it, and the tail of a 200-keyword list buys nothing but tokens. The
#: count it was *not* shown travels with it, so "the rest" is a thing it knows exists.
MAX_ROWS = 20

#: Row keys that name the thing a row is *about* rather than measure it, and the catalog entry
#: each one is headed with in the document — so a paragraph calls a column what the table calls
#: it. They are the design's own ``labels.*`` keys, for exactly that reason.
#:
#: **No two of them may resolve to the same label.** The entry is a dict keyed by the label, so
#: a collision is not a cosmetic clash: the second write wins and the first value disappears
#: from the model's copy without a trace. An audit row carries both its ``section`` and the
#: finding's own ``name``, which is why the latter has a label of its own rather than sharing
#: "Bron" with everything else that names a row.
_LABEL_KEYS = {
    "label": "reporting.doc.source",
    "engine": "reporting.doc.engine",
    "section": "reporting.doc.source",
    "name": "reporting.doc.finding",
    "keyword": "reporting.doc.keyword",
    "landing_page": "reporting.doc.landing_page",
}


def document(
    snapshot: dict[str, Any],
    *,
    locale: str,
    section_titles: dict[str, str] | None = None,
    internal: bool = False,
) -> dict[str, Any]:
    """The whole report, formatted and labelled, ready to be read rather than parsed."""
    titles = section_titles or {}
    period = snapshot.get("period") or {}
    compare = snapshot.get("compare") or {}
    out: dict[str, Any] = {
        "client": (snapshot.get("company") or {}).get("name") or "",
        "period": period.get("label") or "",
    }
    if compare.get("label"):
        out["compared_with"] = compare["label"]
    sections: dict[str, Any] = {}
    for key in snapshot.get("order") or []:
        data = (snapshot.get("sections") or {}).get(key)
        if not data:
            continue
        sections[key] = section(
            data,
            locale=locale,
            title=titles.get(key, key),
            compare_label=compare.get("label"),
            internal=internal,
        )
    out["sections"] = sections
    return out


def section(
    data: dict[str, Any],
    *,
    locale: str,
    title: str,
    compare_label: str | None = None,
    internal: bool = False,
) -> dict[str, Any]:
    """One section as prose-ready data: a title, its totals, and its table.

    Shaped by the **renderer's own** :func:`~render.context.shape_section`, so the model reads
    the table the reader will look at: the same columns, the same folded tail, the same
    humanised event names. Handing it the raw payload instead is how a paragraph comes to
    describe a column the page does not print, or to name thirteen referrers the document folded
    into one line.
    """
    data = shape_section(data, locale, internal=internal)
    out: dict[str, Any] = {"title": title}
    totals = _totals(data, locale, compare_label)
    if totals:
        out["totals"] = totals
    groups = data.get("groups") or []
    if groups:
        out["groups"] = [
            {
                "name": group.get("name") or "",
                "rows": _rows(group.get("rows") or [], data, locale),
            }
            for group in groups
        ]
        return out
    rows = data.get("rows") or []
    if rows:
        out["rows"] = _rows(rows, data, locale)
        if len(rows) > MAX_ROWS:
            out["rows_note"] = (
                f"{MAX_ROWS} of {len(rows)} rows are shown here; the document prints them all."
            )
    if data.get("audited_at"):
        out["audited_at"] = data["audited_at"]
    return out


def _totals(
    data: dict[str, Any], locale: str, compare_label: str | None
) -> list[dict[str, str]]:
    """The headline figures, each beside what it is compared with and how it moved."""
    compare = data.get("compare") or {}
    currency = data.get("currency")
    out: list[dict[str, str]] = []
    # The same order the strip prints them in — a snapshot is JSONB and has none of its own
    # (``render.context._TILE_ORDER``). Two surfaces reading one dict in two different orders is
    # how a paragraph comes to open on the third-most-important figure.
    for metric, value in ordered_metrics(data.get("totals") or {}):
        previous = compare.get(metric)
        # The same predicate the document draws its tiles by, so the model is never handed a
        # figure the page does not print — an "Omzet € 0" it would dutifully write a sentence
        # about, on a report whose reader does not sell anything online.
        if always_zero(value, previous):
            continue
        entry = {
            "metric": metric_label(metric, locale),
            "value": fmt_metric(metric, value, locale, currency),
        }
        if previous is not None:
            entry[compare_label or "compared_with"] = fmt_metric(
                metric, previous, locale, currency
            )
            entry["change"] = fmt_delta(_pct(value, previous), locale)
        out.append(entry)
    return out


def _rows(
    rows: list[Any], data: dict[str, Any], locale: str
) -> list[dict[str, str]]:
    columns = list(data.get("columns") or [])
    currency = data.get("currency")
    channels = (data.get("kind") or "") == "channels"
    out: list[dict[str, str]] = []
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        entry: dict[str, str] = {}
        for key, label_key in _LABEL_KEYS.items():
            if row.get(key):
                value = str(row[key])
                entry[translate(label_key, locale)] = (
                    channel_label(value, locale) if channels and key == "label" else value
                )
        for key in columns:
            if key in row:
                entry[metric_label(key, locale)] = fmt_metric(
                    key, row.get(key), locale, currency
                )
        # A split table carries a comparison the columns do not name (it rides the row), and it
        # is the whole point of half the sentences a report writes.
        for key in ("compare_sessions", "compare_keyEvents", "delta"):
            if key in row and key not in columns and row.get(key) is not None:
                entry[metric_label(key, locale)] = fmt_metric(key, row[key], locale)
        # `status` ("improved" / "declined" / "new") is deliberately left out: it is an English
        # token nothing translates, and a row already carries where a keyword started, where it
        # ended and by how much it moved — which is the same fact, in the document's language.
        out.append(entry)
    return out


def _pct(current: Any, previous: Any) -> float | None:
    try:
        now, before = float(current or 0), float(previous)
    except (TypeError, ValueError):
        return None
    if not before:
        return None
    return round(((now - before) / before) * 100, 1)


__all__ = ["MAX_ROWS", "document", "section"]

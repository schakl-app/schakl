"""Unit tests for the markdown safety helpers (issue #66) — no DB, pure functions."""

from __future__ import annotations

from app.core.richtext import markdown_excerpt, markdown_to_plaintext, sanitize_markdown


def test_sanitize_strips_raw_html_keeps_markdown() -> None:
    dirty = "See **the brief** <script>alert('x')</script> and [doc](https://x.com)"
    clean = sanitize_markdown(dirty)
    assert clean is not None
    assert "<script>" not in clean
    assert "alert" not in clean  # script *content* is dropped, not just the tag
    # Markdown syntax survives untouched — we store source, not stripped text.
    assert "**the brief**" in clean
    assert "[doc](https://x.com)" in clean


def test_sanitize_strips_event_handler_attributes() -> None:
    clean = sanitize_markdown("hello <img src=x onerror=alert(1)> world")
    assert clean is not None
    assert "onerror" not in clean
    assert "<img" not in clean


def test_sanitize_is_idempotent() -> None:
    # A field is edited repeatedly; entity-encoding must not escalate (`&` -> `&amp;` -> ...).
    once = sanitize_markdown("Tom & Jerry, a < b")
    twice = sanitize_markdown(once)
    assert once == twice


def test_sanitize_passes_none_through() -> None:
    assert sanitize_markdown(None) is None


def test_plaintext_flattens_syntax() -> None:
    md = "# Onboarding\n\n- call **client**\n- send [invoice](https://x.com/very/long)\n\n> `note`"
    flat = " ".join(markdown_to_plaintext(md).split())
    assert flat == "Onboarding call client send invoice note"
    # A link resolves to its text before any truncation, so a cut can't sever `](url)`.
    assert "https://" not in flat
    assert "[" not in flat and "]" not in flat


def test_excerpt_flattens_then_caps() -> None:
    md = "**Gebeld** met @[Jan](mention:user:x) over de [offerte](https://x.com)\n\n- akkoord"
    assert markdown_excerpt(md, 200) == "Gebeld met @Jan over de offerte akkoord"
    # The cap counts *readable* characters, so a teaser is never mostly link syntax.
    short = markdown_excerpt(md, 20)
    assert short is not None and len(short) == 20 and short.endswith("…")


def test_excerpt_answers_none_when_nothing_readable_is_left() -> None:
    """``None``, never ``""`` — the callers fall back to a subject or a kind label on it."""
    assert markdown_excerpt(None, 50) is None
    assert markdown_excerpt("", 50) is None
    assert markdown_excerpt("   \n\n  ", 50) is None

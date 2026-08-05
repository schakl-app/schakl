"""HTML → markdown for received e-mail bodies (``app/core/htmlmd.py``).

Pure functions, so these are pure unit tests — no tenant, no database. What they pin down is
the judgement calls: what survives, what degrades, and what must never be reinterpreted.
"""

from __future__ import annotations

from app.core.htmlmd import html_to_markdown, referenced_cids, rewrite_cid_images


def test_blank_input_is_none() -> None:
    assert html_to_markdown(None) is None
    assert html_to_markdown("   ") is None
    assert html_to_markdown("<div>  </div>") is None


def test_paragraphs_and_inline_emphasis() -> None:
    md = html_to_markdown(
        "<p>Beste Stan,</p><p>Hierbij de <strong>offerte</strong> en een "
        "<em>korte</em> toelichting.</p>"
    )
    assert md == "Beste Stan,\n\nHierbij de **offerte** en een *korte* toelichting."


def test_lists_keep_their_shape_and_nesting() -> None:
    md = html_to_markdown(
        "<ul><li>Hosting</li><li>SSL<ul><li>wildcard</li></ul></li></ul>"
        "<ol><li>Eerst</li><li>Daarna</li></ol>"
    )
    assert md == (
        "- Hosting\n"
        "- SSL\n"
        "    - wildcard\n"
        "\n"
        "1. Eerst\n"
        "2. Daarna"
    )


def test_headings_never_render_above_h3() -> None:
    """The app's renderer draws h3 and below; a faithful `#` would be dropped as noise, and a
    dropped heading is lost content."""
    md = html_to_markdown("<h1>Titel</h1><h2>Sub</h2><h4>Diep</h4>")
    assert md == "### Titel\n\n### Sub\n\n#### Diep"


def test_blockquote_becomes_a_quote_the_trail_splitter_folds() -> None:
    md = html_to_markdown(
        "<p>Zie hieronder.</p>"
        '<blockquote class="gmail_quote"><p>Op 3 aug schreef Klant:</p>'
        "<p>Kun je een voorstel sturen?</p></blockquote>"
    )
    assert md == (
        "Zie hieronder.\n"
        "\n"
        "> Op 3 aug schreef Klant:\n"
        "\n"
        "> Kun je een voorstel sturen?"
    )


def test_links_keep_safe_schemes_and_unwrap_the_rest() -> None:
    assert html_to_markdown('<p>Zie <a href="https://x.test/a">de site</a>.</p>') == (
        "Zie [de site](https://x.test/a)."
    )
    assert html_to_markdown('<p><a href="mailto:a@b.test">mail ons</a></p>') == (
        "[mail ons](mailto:a@b.test)"
    )
    # A scheme that cannot be followed keeps the words and loses the link.
    assert html_to_markdown('<p><a href="javascript:alert(1)">klik</a></p>') == "klik"
    assert html_to_markdown("<p><a>naamloos</a></p>") == "naamloos"


def test_script_and_style_lose_their_contents_entirely() -> None:
    md = html_to_markdown(
        "<style>.x{color:red}</style><p>Hallo</p><script>alert('x')</script>"
    )
    assert md == "Hallo"


def test_received_text_is_escaped_not_reinterpreted() -> None:
    """A sender's asterisks were never markdown. Preserving what they wrote is the whole
    point; rendering `*belangrijk*` as italics is a different message."""
    md = html_to_markdown("<p>Dit is *belangrijk* en kost 2_000 euro [zie bijlage]</p>")
    assert md == r"Dit is \*belangrijk\* en kost 2\_000 euro \[zie bijlage\]"

    # A line that would read as a list item or a heading is escaped at the start too.
    assert html_to_markdown("<p>- geen lijst</p>") == r"\- geen lijst"
    assert html_to_markdown("<p># geen kop</p>") == r"\# geen kop"


def test_cid_images_survive_and_remote_images_do_not() -> None:
    """A tracking pixel is an image: rendering one tells the sender the mail was opened."""
    md = html_to_markdown(
        '<p><img src="cid:logo@bureau" alt="Bureau"> en '
        '<img src="https://track.test/p.gif" alt="" width="1"> en '
        '<img src="https://cdn.test/foto.png" alt="Foto van de gevel"></p>'
    )
    assert md == "![Bureau](cid:logo@bureau) en en Foto van de gevel"
    assert referenced_cids(md) == {"logo@bureau"}


def test_cid_rewrite_points_at_a_stored_file_or_degrades_to_alt() -> None:
    md = "![Bureau](cid:logo@bureau) en ![Plattegrond](cid:weg@bureau)"
    rewritten = rewrite_cid_images(md, {"logo@bureau": "11111111-1111-1111-1111-111111111111"})
    assert rewritten == (
        "![Bureau](file:11111111-1111-1111-1111-111111111111) en Plattegrond"
    )
    # An angle-bracketed Content-ID, as the header spells it, resolves too.
    assert rewrite_cid_images("![L](cid:<a@b>)", {"a@b": "22222222-2222-2222-2222-222222222222"})


def test_a_data_table_becomes_a_grid() -> None:
    md = html_to_markdown(
        "<table><tr><th>Dienst</th><th>Bedrag</th></tr>"
        "<tr><td>Hosting</td><td>120,00</td></tr>"
        "<tr><td>SSL</td><td>45,00</td></tr></table>"
    )
    assert md == (
        "| Dienst | Bedrag |\n"
        "| --- | --- |\n"
        "| Hosting | 120,00 |\n"
        "| SSL | 45,00 |"
    )


def test_a_layout_table_flattens_to_lines() -> None:
    """Newsletter HTML is nested tables used for arrangement. A grid of them reads worse than
    the text it replaces, so only positive evidence makes a grid."""
    md = html_to_markdown(
        "<table><tr><td><table><tr><td>Beste Stan,</td></tr>"
        "<tr><td>Hierbij onze nieuwsbrief.</td></tr></table></td></tr></table>"
    )
    assert md == "Beste Stan, Hierbij onze nieuwsbrief."

    # A single row of prose is not data either.
    long_cell = "x" * 120
    assert html_to_markdown(f"<table><tr><td>{long_cell}</td><td>b</td></tr></table>") == (
        f"{long_cell} b"
    )


def test_line_breaks_and_preformatted_text() -> None:
    assert html_to_markdown("<p>Regel een<br>Regel twee</p>") == "Regel een  \nRegel twee"
    assert html_to_markdown("<pre>def x():\n    return 1</pre>") == (
        "```\ndef x():\n    return 1\n```"
    )


def test_malformed_html_still_yields_its_words() -> None:
    """One unparseable body must not lose the message."""
    md = html_to_markdown("<div><p>Onaf<span>gesloten<div>Toch tekst")
    assert md is not None
    assert "Onaf" in md and "Toch tekst" in md

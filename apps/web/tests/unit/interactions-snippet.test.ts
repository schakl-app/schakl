/**
 * A Gmail snippet is not display text (#263).
 *
 * It arrives HTML-escaped and padded with the message's invisible preheader, and it runs to
 * ~200 characters — pasted into a list row it reads as escape codes and swallows the line. The
 * API cleans it at the ingest seam (`gmail/matching.clean_snippet`, covered on the Python
 * side); this covers the browser's own pass, which every already-stored row still needs.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { cleanSnippet, snippetPreview } from "../../src/lib/modules/interactions/snippet.ts";

describe("cleanSnippet", () => {
  test("decodes the entities Gmail escapes", () => {
    assert.equal(
      cleanSnippet("Bedankt voor &#39;s ochtends &amp; morgen &quot;even&quot; bellen"),
      `Bedankt voor 's ochtends & morgen "even" bellen`,
    );
  });

  test("decodes hex character references too", () => {
    assert.equal(cleanSnippet("caf&#xe9; &#x27;t Zand"), "café 't Zand");
  });

  test("drops the invisible preheader padding and collapses the whitespace", () => {
    assert.equal(
      cleanSnippet("Nieuwsbrief\u200b\u200b\u200b   juli\n\n2026\u00ad\ufeff"),
      "Nieuwsbrief juli 2026",
    );
  });

  test("leaves something that only looks like an entity alone", () => {
    assert.equal(cleanSnippet("R&D budget & marge <5%"), "R&D budget & marge <5%");
  });

  test("an empty or absent snippet is an empty string, never 'null'", () => {
    assert.equal(cleanSnippet(null), "");
    assert.equal(cleanSnippet(undefined), "");
    assert.equal(cleanSnippet("   \u200b  "), "");
  });
});

describe("snippetPreview", () => {
  test("a short snippet is returned whole, with no ellipsis", () => {
    assert.equal(snippetPreview("Kort berichtje"), "Kort berichtje");
  });

  test("a long one is cut on a word boundary, never mid-word", () => {
    const preview = snippetPreview(
      "Beste Jan, hierbij de offerte voor de nieuwe website zoals besproken",
    );
    assert.ok(preview.endsWith("…"), preview);
    assert.ok(preview.length <= 51, `too long: ${preview.length}`);
    // Word boundary: everything before the ellipsis is whole words of the original.
    assert.ok(
      "Beste Jan, hierbij de offerte voor de nieuwe website zoals besproken".startsWith(
        preview.slice(0, -1),
      ),
      preview,
    );
    assert.ok(!preview.slice(0, -1).endsWith(" "), preview);
  });

  test("one very long word still yields a teaser rather than nothing", () => {
    const preview = snippetPreview(`${"a".repeat(80)} en de rest`, 20);
    assert.equal(preview, `${"a".repeat(20)}…`);
  });

  test("cleans before it counts, so escape codes never eat the budget", () => {
    // Raw, this is 60 characters; decoded it is short enough to survive whole.
    assert.equal(snippetPreview("Tot &#39;s middags&nbsp;dan&#33;", 30), "Tot 's middags dan!");
  });
});

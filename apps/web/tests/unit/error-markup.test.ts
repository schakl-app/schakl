/**
 * The two values the standalone error document interpolates (`$lib/core/errors/markup.ts`).
 *
 * That page is built by string concatenation, because it has to render when the framework, the
 * stylesheet and the API are all unavailable (docs/DEPLOY.md). Concatenation is why these two
 * helpers exist and why they are tested apart from the renderer: everything they touch is
 * attacker-reachable — the requested path, and the brand name and logo URL a tenant typed into
 * Huisstijl — and on a page with no template engine, an unescaped value is the whole bug.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { esc, safeRetryHref } from "../../src/lib/core/errors/markup.ts";

describe("esc", () => {
  test("escapes the five characters that can break out of text or a quoted attribute", () => {
    assert.equal(esc(`<script>&"'`), "&lt;script&gt;&amp;&quot;&#39;");
  });

  test("a brand name cannot close the tag it sits in", () => {
    // Tenant-typed, rendered inside <div class="brand"> and again as an <img alt="…">.
    const out = esc("</div><script>alert(1)</script>");
    assert.ok(!out.includes("<script>"));
    assert.ok(!out.includes("</div>"));
  });

  test("leaves ordinary text alone", () => {
    assert.equal(esc("Bureau Föhn & Zoon"), "Bureau Föhn &amp; Zoon");
  });
});

describe("safeRetryHref", () => {
  test("keeps a site-relative path, query and all", () => {
    assert.equal(safeRetryHref("/tasks?page=3"), "/tasks?page=3");
    assert.equal(safeRetryHref("/"), "/");
  });

  test("refuses anything that could leave the site", () => {
    // The page renders on a 503 that *any* URL can produce, so the requested path is chosen by
    // whoever made the request. A scheme-relative URL is the shape that looks like a path and
    // is not one; a backslash is the same trick against a parser that treats it as a separator.
    assert.equal(safeRetryHref("//evil.example/login"), null);
    assert.equal(safeRetryHref("/\\evil.example"), null);
    assert.equal(safeRetryHref("https://evil.example"), null);
    assert.equal(safeRetryHref("javascript:alert(1)"), null);
    assert.equal(safeRetryHref("tasks"), null); // not rooted: resolves against the error URL
  });

  test("nothing to offer is null, not an empty href", () => {
    // An empty `href` resolves to the current document — the one page we know is broken.
    assert.equal(safeRetryHref(""), null);
    assert.equal(safeRetryHref(undefined), null);
    assert.equal(safeRetryHref(null), null);
  });
});

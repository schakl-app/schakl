/**
 * The pagination contract every list shares (`$lib/core/table/paging.ts`).
 *
 * These are the cases that turn a pager into a liar: a page number the URL cannot honour, a size
 * larger than the API will serve, a filter that keeps the old page number, and the elision that
 * decides which page links are drawn. All four are invisible in a screenshot and obvious here.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  coercePageSize,
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  pageCount,
  pageHref,
  pageWindow,
  resetPage,
  resolvePaging,
} from "../../src/lib/core/table/paging.ts";

const url = (search: string) => new URL(`https://acme.example/companies${search}`);

describe("resolvePaging", () => {
  test("a bare URL is page 1 at the default size", () => {
    assert.deepEqual(resolvePaging(url("")), { page: 1, limit: DEFAULT_PAGE_SIZE, offset: 0 });
  });

  test("the saved preference is the default, and the URL overrides it", () => {
    assert.equal(resolvePaging(url(""), { page_size: 100 }).limit, 100);
    assert.equal(resolvePaging(url("?size=25"), { page_size: 100 }).limit, 25);
  });

  test("the offset is derived from the page, so the two can never disagree", () => {
    assert.deepEqual(resolvePaging(url("?page=4&size=25")), { page: 4, limit: 25, offset: 75 });
  });

  test("a size the API would refuse is clamped, not sent", () => {
    // `Query(..., le=200)` on every list route: asking for 5000 is a 422, and a 422 on a list
    // load is a blank screen rather than a big page.
    assert.equal(resolvePaging(url("?size=5000")).limit, MAX_PAGE_SIZE);
  });

  test("junk degrades to the default rather than throwing on a page load", () => {
    assert.equal(resolvePaging(url("?size=abc")).limit, DEFAULT_PAGE_SIZE);
    assert.equal(resolvePaging(url("?size=0")).limit, DEFAULT_PAGE_SIZE);
    assert.equal(resolvePaging(url("?page=abc")).page, 1);
    assert.equal(resolvePaging(url("?page=-3")).page, 1);
    assert.equal(resolvePaging(url(""), { page_size: -10 }).limit, DEFAULT_PAGE_SIZE);
  });
});

describe("coercePageSize", () => {
  test("a caller's own fallback wins over the global default", () => {
    assert.equal(coercePageSize(null, 25), 25);
    assert.equal(coercePageSize("100", 25), 100);
  });
});

describe("pageCount", () => {
  test("an empty list is one page, so 'page 1 of 0' is unsayable", () => {
    assert.equal(pageCount(0, 50), 1);
  });

  test("a partial last page counts", () => {
    assert.equal(pageCount(101, 50), 3);
    assert.equal(pageCount(100, 50), 2);
  });
});

describe("pageHref", () => {
  test("every other parameter survives a page step", () => {
    assert.equal(pageHref(url("?q=acme&sort=-name"), 3), "/companies?q=acme&sort=-name&page=3");
  });

  test("page 1 is the bare URL, so the first page has one address", () => {
    assert.equal(pageHref(url("?q=acme&page=7"), 1), "/companies?q=acme");
  });
});

describe("resetPage", () => {
  test("a filter change drops the page but keeps the size", () => {
    // Page 7 of the old filter is not page 7 of the new one — usually it is nothing at all,
    // and an empty page reads as "the filter found nothing".
    const next = resetPage(url("?page=7&size=100&q=acme"));
    assert.equal(next.searchParams.get("page"), null);
    assert.equal(next.searchParams.get("size"), "100");
  });
});

describe("pageWindow", () => {
  test("a short list draws every page", () => {
    assert.deepEqual(pageWindow(2, 4), [1, 2, 3, 4]);
  });

  test("a long list elides, always keeping the first and last", () => {
    assert.deepEqual(pageWindow(10, 40), [1, null, 9, 10, 11, null, 40]);
  });

  test("a gap of exactly one is spelled out — '1 … 3' is longer than '1 2 3'", () => {
    // The window is 3–5; page 2 is the lone hole between it and page 1, so it is drawn.
    assert.deepEqual(pageWindow(4, 9), [1, 2, 3, 4, 5, null, 9]);
  });

  test("a single page is itself", () => {
    assert.deepEqual(pageWindow(1, 1), [1]);
  });
});

/**
 * What an error page says, per status (`$lib/core/errors/copy.ts`).
 *
 * There are three renderers of this table — the in-app `+error.svelte`, the standalone document
 * the hook serves when the API is unreachable, and the API's own Python twin for when the SSR app
 * is the thing that is gone (`app/core/errorpage.py`). Only the *copy* is shared, so only the
 * copy can be tested in one place, and the fallbacks are the part worth pinning: a status arrives
 * here from a URL segment the edge templated, from SvelteKit's `page.status`, and from Traefik.
 * Anything can turn up, and inventing a sentence for it is worse than admitting we have none.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { errorCopy } from "../../src/lib/core/errors/copy.ts";

describe("errorCopy", () => {
  test("names a specific page for the statuses a visitor actually meets", () => {
    assert.equal(errorCopy(404).titleKey, "errors.page.not_found.title");
    assert.equal(errorCopy(403).titleKey, "errors.page.forbidden.title");
    assert.equal(errorCopy(401).titleKey, "errors.page.unauthorized.title");
    assert.equal(errorCopy(429).titleKey, "errors.page.rate_limited.title");
  });

  test("a gateway status is 'briefly unavailable', never 'something went wrong'", () => {
    // This is what a rolling redeploy looks like from outside (docs/DEPLOY.md). Telling an
    // agency's client that something broke, over a planned two-minute rollover, sends them to
    // the phone — and it is not even true.
    for (const status of [502, 503, 504]) {
      assert.equal(errorCopy(status).titleKey, "errors.page.unavailable.title", `${status}`);
      assert.equal(errorCopy(status).retryable, true, `${status}`);
    }
  });

  test("an unrecognised status falls back by range rather than inventing a sentence", () => {
    assert.equal(errorCopy(599).titleKey, "errors.page.server.title");
    assert.equal(errorCopy(418).titleKey, "errors.page.generic.title");
    // Not an error at all: reached only through a hand-typed or stale URL, and the generic
    // sentence is the honest answer — anything else would claim to know what happened.
    assert.equal(errorCopy(200).titleKey, "errors.page.generic.title");
    assert.equal(errorCopy(Number.NaN).titleKey, "errors.page.generic.title");
  });

  test("only the transient failures offer a retry", () => {
    // "Probeer opnieuw" is a promise. A 404 and a 403 answer identically however many times
    // they are asked, so offering it there is a control that always refuses.
    assert.equal(errorCopy(404).retryable, false);
    assert.equal(errorCopy(403).retryable, false);
    assert.equal(errorCopy(500).retryable, true);
    assert.equal(errorCopy(429).retryable, true);
  });
});

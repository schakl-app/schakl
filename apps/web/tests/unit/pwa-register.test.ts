/**
 * The service worker gets installed, and installed for the whole origin (#309 follow-up).
 *
 * The app shipped a generated `sw.js` that nothing ever registered: `injectRegister: "auto"`
 * places its `<script>` through Vite's `transformIndexHtml`, and SvelteKit bakes `app.html`
 * without ever calling that hook. Every existing test stubbed `navigator.serviceWorker`, so all
 * of them passed against a browser that had no worker at all — which is why the assertion worth
 * having here is not "a worker exists" but **"we asked for one, with these exact arguments"**.
 *
 * The arguments are the whole point. The plugin's own `registerSW.js` said `register("./sw.js",
 * { scope: "./" })` — relative, because SvelteKit builds the client with Vite's `base` set to
 * `"./"` — which resolves against the *page*: `/instellingen/sw.js` on the settings screen, a
 * 404, and a worker scoped to one subtree anywhere it did load. A regression here would look
 * exactly like the bug it replaced: nothing thrown, nothing logged, one screen quietly saying
 * this browser cannot receive notifications.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { afterEach, describe, mock, test } from "node:test";

import { registerServiceWorker, SERVICE_WORKER_URL } from "../../src/lib/core/pwa.ts";

/** `navigator` is a getter on `globalThis` in Node, so it is replaced rather than assigned. */
function define(name: string, value: unknown): void {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

afterEach(() => {
  Reflect.deleteProperty(globalThis, "navigator");
});

describe("registerServiceWorker", () => {
  test("registers the generated worker for the whole origin", async () => {
    const registration = { scope: "https://acme.example/" };
    const register = mock.fn(async () => registration);
    define("navigator", { serviceWorker: { register } });

    assert.equal(await registerServiceWorker(), registration);
    assert.equal(register.mock.callCount(), 1);
    // Absolute, and root-scoped: a relative path would resolve against whichever page happened
    // to be open, and a subtree scope would leave most of the app uncovered.
    assert.deepEqual(register.mock.calls[0].arguments, ["/sw.js", { scope: "/" }]);
    assert.equal(SERVICE_WORKER_URL, "/sw.js");
  });

  test("is a no-op where the browser has no service workers", async () => {
    define("navigator", {});

    assert.equal(await registerServiceWorker(), null);
  });

  test("a refused registration is swallowed, never an unhandled rejection", async () => {
    // A private window, an enterprise policy, an origin the browser does not trust. The app
    // works without a worker; the settings screen is what tells the user, not the console.
    const register = mock.fn(async () => {
      throw new TypeError("Failed to register a ServiceWorker");
    });
    define("navigator", { serviceWorker: { register } });

    assert.equal(await registerServiceWorker(), null);
  });
});

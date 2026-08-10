/**
 * `status()` always answers (#309 follow-up).
 *
 * `navigator.serviceWorker.ready` resolves on success and has no other outcome: with no worker
 * registered for the scope it stays pending for the life of the page. Awaiting it bare therefore
 * produced no error, no rejection and no log line — just a settings section sitting on its
 * loading sentence forever, in every `pnpm dev` session and in any real tab whose registration
 * had failed. Nothing in a functional test can see the difference between "slow" and "never",
 * which is exactly why the floor is worth pinning.
 *
 * Run with `pnpm web test:unit` (node's built-in runner strips the types; no vitest here).
 */
import assert from "node:assert/strict";
import { afterEach, describe, mock, test } from "node:test";

import { REGISTRATION_TIMEOUT_MS, status } from "../../src/lib/modules/notifications/push.ts";

/** The narrow slice of the browser `push.ts` reads. `ready` is whatever the case under test needs. */
function stubBrowser(options: {
  ready: Promise<unknown>;
  permission?: NotificationPermission;
}): void {
  const notification = {
    permission: options.permission ?? "default",
    requestPermission: mock.fn(),
  };
  const serviceWorker = { ready: options.ready };
  define("navigator", { serviceWorker, userAgent: "Mozilla/5.0 (X11; Linux x86_64) Chrome/131" });
  define("Notification", notification);
  define("window", {
    PushManager: class {},
    Notification: notification,
    navigator: globalThis.navigator,
  });
}

/** `navigator` is a getter on `globalThis` in Node, so it is replaced rather than assigned. */
function define(name: string, value: unknown): void {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

afterEach(() => {
  for (const name of ["navigator", "Notification", "window"]) {
    Reflect.deleteProperty(globalThis, name);
  }
  mock.timers.reset();
});

describe("status", () => {
  test("settles on `no-worker` when no service worker ever registers", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    // A promise that never settles — precisely what `ready` is when nothing is registered.
    stubBrowser({ ready: new Promise(() => {}) });

    const pending = status();
    t.mock.timers.tick(REGISTRATION_TIMEOUT_MS);

    assert.deepEqual(await pending, { state: "no-worker", endpoint: null });
  });

  test("reports `off` once a worker is there but nothing is subscribed", async () => {
    stubBrowser({
      ready: Promise.resolve({ pushManager: { getSubscription: async () => null } }),
    });

    assert.deepEqual(await status(), { state: "off", endpoint: null });
  });

  test("reports `on` with the endpoint, so the device list can mark its own row", async () => {
    const subscription = { endpoint: "https://push.example/abc" };
    stubBrowser({
      ready: Promise.resolve({ pushManager: { getSubscription: async () => subscription } }),
    });

    assert.deepEqual(await status(), { state: "on", endpoint: "https://push.example/abc" });
  });

  test("`denied` short-circuits: a blocked browser is never kept waiting on a worker", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    stubBrowser({ ready: new Promise(() => {}), permission: "denied" });

    // No tick: this must not depend on the timeout at all.
    assert.deepEqual(await status(), { state: "denied", endpoint: null });
  });
});

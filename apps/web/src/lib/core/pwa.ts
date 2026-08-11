/**
 * Installing the service worker (#309 follow-up).
 *
 * The PWA plugin *generates* a worker — the precache, the runtime caching, and the `push` /
 * `notificationclick` listeners `static/push-sw.js` contributes — but generating one and
 * installing one are different acts, and nothing in this app ever did the second.
 * `injectRegister: "auto"` looks exactly like the thing that does: it emits a `registerSW.js`
 * and injects a `<script>` tag for it through Vite's `transformIndexHtml` hook. **SvelteKit
 * bakes `app.html` itself and never calls that hook** — so the tag was never written, the file
 * was never fetched, and every browser ran with no worker at all, in dev, in preview and in the
 * production image alike. `/sw.js` answered 200 the whole time; nobody had ever asked for it.
 *
 * The generalisation is the one `docs/MCP.md` already records for `/api/docs`: **when a surface
 * only exists if something else wires it up, assert the wiring, not the artefact.** Every test
 * here stubbed `navigator.serviceWorker`, so all of them passed against a browser that had none.
 *
 * Two details of the registration below are load-bearing:
 *
 *  - **The path is absolute.** The emitted `registerSW.js` said `register("./sw.js", { scope:
 *    "./" })`, because SvelteKit builds the client with Vite's `base` set to `"./"`
 *    (`paths.relative` is on by default) and the plugin composes the path out of it. Resolved
 *    against the *page*, that is `/instellingen/sw.js` on the settings screen — a 404 and a
 *    rejected registration, and on the pages where it would have loaded, a worker scoped to one
 *    subtree. A worker covers the whole origin or it is not worth registering.
 *  - **A failure is swallowed.** This is best-effort: a private window, an enterprise policy, a
 *    browser without service workers. None of that is worth an unhandled rejection in the
 *    console of an app that works perfectly well without a worker — the one screen that needs
 *    one asks `push.ts` whether it is there and says so in words.
 */

/** Where `vite.config.ts` has the PWA plugin write the generated worker: the origin root. */
export const SERVICE_WORKER_URL = "/sw.js";

/**
 * Install the generated service worker for the whole origin.
 *
 * Returns the registration, or `null` when this browser has no service workers or refused the
 * one we asked for. Safe to call more than once — the browser treats a repeat registration of
 * the same script and scope as a no-op.
 */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: "/" });
  } catch {
    return null;
  }
}

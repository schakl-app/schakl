import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { SvelteKitPWA } from "@vite-pwa/sveltekit";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit(),
    SvelteKitPWA({
      registerType: "autoUpdate",
      // Branding is per-tenant and resolved at runtime, so we serve a dynamic
      // /manifest.webmanifest route instead of a build-time manifest (Golden Rule 4).
      manifest: false,
      // **The plugin cannot register the worker here, so we do it ourselves** — see
      // `src/lib/core/pwa.ts` and the root `+layout.svelte`. Every `injectRegister` mode works
      // through Vite's `transformIndexHtml` hook, and SvelteKit bakes `app.html` without ever
      // calling it, so `"auto"` emitted a `registerSW.js` that no page has ever loaded: the
      // app shipped a service worker nothing installed. `false` stops emitting the dead file.
      injectRegister: false,
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,webp,woff2}"],
        // Browser push (#309) needs `push` + `notificationclick` listeners inside the service
        // worker, and workbox generates that worker for us. Importing a static file is the
        // smallest way in: the precache manifest and every runtime caching strategy stay
        // workbox's, and the diff is one line plus `static/push-sw.js`. Switching to
        // `injectManifest` would hand us all of that to maintain, for two event listeners, in
        // an app already installed on real devices.
        importScripts: ["/push-sw.js"],
        // `registerType: "autoUpdate"` is only *half* a setting: the plugin turns it into these
        // two flags — the thing that actually makes a new deploy's worker take over instead of
        // queuing behind the old one — and it does so only while `injectRegister` is left at
        // its default. Turning that off above would have quietly made "autoUpdate" a word in a
        // config file, so the constraint is stated as the constraint rather than inherited.
        skipWaiting: true,
        clientsClaim: true,
        // No offline navigation fallback. The plugin's default points one at "/", and workbox
        // then throws `non-precached-url` while the worker evaluates, because every page here
        // is server-rendered per tenant and per session and nothing prerenders "/" into the
        // precache. The throw lands inside a promise chain, so it surfaced as neither a failed
        // install nor a console error — just a worker whose last line never ran. There is no
        // shell to fall back to, and precaching one would serve a stale, wrong-tenant, possibly
        // signed-in page; so this app has no offline navigation, and now says so.
        navigateFallback: null,
      },
    }),
  ],
  // Bundle server-imported runtime deps into the adapter-node output so the production
  // image is self-contained (no node_modules needed at runtime). `Markdown.svelte` renders
  // the escaped source during SSR, so `markdown.ts` (and its `dompurify` / `marked` imports)
  // is reachable on the server and must be bundled too, not left as a bare `import` the
  // node_modules-less runtime image can't resolve (#66).
  ssr: {
    noExternal: ["openapi-fetch", "dompurify", "marked", "libphonenumber-js"],
  },
  server: {
    host: true,
    port: 5173,
  },
});

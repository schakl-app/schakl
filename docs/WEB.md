# Web app — build & runtime

> Read before adding an npm dependency to `apps/web` that any server-side code imports.

## The production image ships no `node_modules`

`apps/web/Dockerfile` builds the SvelteKit app with `@sveltejs/adapter-node`, and the runtime
stage copies **only** `build/` and `package.json` — **no `node_modules`**. The server bundle in
`build/` must therefore be **self-contained**: every dependency reachable at runtime has to be
*bundled into* `build/`, not left as a bare `import "x"` that Node resolves against a
`node_modules` directory that isn't in the image.

## Server-reachable runtime deps go in `ssr.noExternal`

Vite externalizes `node_modules` dependencies from the SSR build by default. A dependency that is
imported — directly or transitively — from **server-reachable** code and left external becomes an
`import "x"` the runtime image can't resolve, and the `web` container crash-loops on boot:

```
Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'x' imported from /app/build/server/...
```

Force Vite to bundle it, in `apps/web/vite.config.ts`:

```ts
ssr: {
  noExternal: ["openapi-fetch", "dompurify", "marked"],
},
```

### "Server-reachable" is broader than "runs on the server"

A module is server-reachable if it is imported by a `*.server.ts`, a `+layout`/`+page` `load`,
`hooks.server.ts`, or **any component that SSR-renders** — even when the dependency only *executes*
in the browser. `markdown.ts` has a top-level `import DOMPurify from "dompurify"`; `Markdown.svelte`
renders the escaped source during SSR, so that import is pulled into the server bundle even though
`DOMPurify.sanitize` only runs after mount (issue #66). Being browser-only in *behaviour* does not
keep a static `import` out of the server bundle — only never being imported from server-reachable
code does. A genuinely client-only dependency (imported solely from code behind a `browser` guard
or a dynamic `import()` after mount) needs nothing here.

## How to catch it

`pnpm run check` and `pnpm web build` do **not** catch this: in the dev tree `node_modules` exists,
so the external import resolves and both pass. It only fails in the image, which has none. So
**build and boot the web image** — this is part of the Definition of done in `docs/WORKFLOW.md`:

```bash
docker compose -f infra/compose.yaml build web
docker compose -f infra/compose.yaml up -d web
docker compose -f infra/compose.yaml ps      # web must NOT be restarting
docker logs schakl-web-1                      # a crash names the missing package
```

The crash message names the unresolved package; add it to `noExternal` and rebuild.

## The service worker is registered by us, not by the PWA plugin

`SvelteKitPWA` **generates** a service worker; it does not install one. Every `injectRegister`
mode places its `<script>` through Vite's `transformIndexHtml` hook, and **SvelteKit never calls
that hook** — it bakes `app.html` into the server bundle itself. So `injectRegister: "auto"`
emitted a `registerSW.js` into the client output that no page has ever loaded: `/sw.js` answered
`200` in every deployment, and no browser had once been asked for it. The app was a PWA that
precached nothing, updated nothing and could receive no push, and the only place that said so was
Instellingen → Meldingen, which is the one screen that asks whether a worker exists.

So the registration is one function, `src/lib/core/pwa.ts`, called from the **root** layout —
never `(app)`, because a worker covers an origin and the login screen and client portal belong to
the installed app too. Three things about it are not stylistic:

- **`injectRegister: false`.** The emitted file is dead weight, and leaving it there invites
  someone to conclude the wiring exists.
- **`skipWaiting` + `clientsClaim` are stated explicitly.** The plugin derives them from
  `registerType: "autoUpdate"` *only while `injectRegister` is at its default* — turn one off and
  the other silently becomes a word in a config file, with each deploy's worker queuing behind the
  previous one forever. State the constraint as the constraint (`CLAUDE.md` §11).
- **`navigateFallback: null`.** The default points a precache handler at `/`, and workbox throws
  `non-precached-url` while the worker evaluates, because every page here is SSR'd per tenant and
  per session and nothing prerenders `/`. The throw lands inside a promise chain, so it is neither
  a failed install nor a console error — the worker's last line just never runs. There is no shell
  to fall back to and precaching one would serve a stale, wrong-tenant, possibly signed-in page.

### How to catch it

`pnpm run check`, the unit tests and the build all passed for the entire life of the bug — the
tests stub `navigator.serviceWorker`, which is precisely the thing that was missing. `pnpm
pwa:check` (CI, after the web build) reads the **build output** instead and asks the four
questions no source file can answer: does some client bundle call `serviceWorker.register`, does
it use an absolute `/sw.js`, does everything `sw.js` imports exist, and does every URL bound to a
precache handler appear in the precache manifest.

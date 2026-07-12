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

# @schakl/site — project website + docs

The public site for **schakl.** (issue #135): an Astro landing site with Starlight docs,
fully static output, Dutch (root) + English. This is the one place the schakl. brand lives —
the product itself is white-label.

## Everything is content

All user-visible content — brand, colors, logo, nav, footer, landing copy, feature cards,
and the docs pages themselves — lives as JSON/MDX in this package and is edited through
**Sveltia CMS** (git-based, **first-class i18n**: one entry, a locale switcher in the
editor, per-entry locale opt-out, optional DeepL assist — translations stay manual):

```bash
pnpm site cms          # dev server; the CMS is at http://localhost:4321/admin
```

On `/admin`, choose **Work with Local Repository** (Chrome/Edge) and pick the repo folder —
saves write straight into the working tree, review with `git diff`, commit like any change.
(Known dev-server quirk: named static pages like `/admin` log a **404 status while rendering
fine** — every non-index page outside Starlight does, only in `astro dev`; `astro preview`
and production serve 200. Ignore the status, trust the page.)
Remote sign-in (edit from anywhere, drafts as branches) needs a small token-exchange
service (`sveltia-cms-auth`) and is decided together with the deploy target.

| What | Where | CMS surface |
| --- | --- | --- |
| Brand name, logo (+dark variant), favicon, **colors**, nav, footer | `src/data/settings/site.json` | Site-instellingen |
| Landing **blocks** (hero, feature grid, tour, integrations strip, text+bullets, CTA — add/reorder freely; one entry, both locales) | `src/data/landing.json` | Landingspagina |
| Feature cards (NL+EN fields side by side) | `src/data/features/*.json` | Functie-kaarten |
| Integration cards (`/integrations/` + one page each) | `src/data/integrations/*.json` | Koppelingen |
| **Free-form pages** (MDX, own URL, site chrome; one entry, locale switcher) | `src/content/pages/{nl,en}/*` | Pagina's |
| Docs pages (one entry, locale switcher; paths pair the translations) | `src/content/docs/{nl,en}/docs/**` | Documentatie |

### What is content and what is code

The split is deliberate and worth knowing before you go looking for something in the CMS:

- **Content** is the words: card copy, landing blocks, docs prose, integration write-ups.
- **Code** is the *shape*: which feature groups exist and in what order (`src/lib/features.ts`),
  which integration categories exist (`src/lib/integrations.ts`), and the animated product demos
  (`src/components/showcase/Demo*.astro`), which recreate the real app UI and therefore change
  when the app does — not when a marketeer wants a different sentence.

A card names things in code by string: an icon (`lucide`), a demo (`demo`), a category. Nothing in
`astro build` checks those, so `pnpm site:content` does — see **Checks** below.

Creating a page with slug `prijzen` publishes `/prijzen/` and `/en/prijzen/` on the next
build — the slug is shared across locales (that's what pairs the translations); link it by
adding a nav item in Site-instellingen. Don't use the slugs `docs`, `nl` or `en`. Landing
sections carry an optional *anker* so nav `#`-links keep working when blocks move. Docs
URLs carry their locale (`/nl/docs/…`, `/en/docs/…`); `/docs/…` redirects to Dutch.

The accent colors flow into the landing pages *and* the Starlight docs theme
(`astro.config.mjs` reads the settings at build time); the logo is referenced by path from
the same file. Changing them in the CMS and rebuilding restyles the whole site — no code.
The CMS itself is a single static page (`/admin`); its bundle is copied from
`node_modules/@sveltia/cms` by the `sync-cms` script, its content model lives in
`public/sveltia/config.yml`.

## Commands

```bash
pnpm site dev          # landing + docs, no CMS
pnpm site cms          # same + the Sveltia admin at /admin
pnpm site build        # static build to dist/ — this is what deploys
```

## Checks

Four, and each exists because `astro build` renders the broken version happily. CI runs all four
(the `site` job in `.github/workflows/ci.yml`).

```bash
pnpm docs:check        # nl/en parity, expected pages, unlocalised /docs/ links, MDX brace hazards
pnpm site:content      # every icon / demo / category / docs link a data file names actually exists
pnpm site:order        # feature-card `order` agrees with the grouping (--check to fail instead of fix)
pnpm site:build && pnpm site:links   # every internal link in dist/ resolves to a real page
```

Two of the failures they guard shipped for real, which is why they are checks and not conventions:

- **Docs links carry their locale.** The tree is symmetric `/nl/docs/…` + `/en/docs/…` and only the
  bare `/docs` redirects, so a link to `/docs/admin/installation/` is a 404. Three of them sat on
  the first page a Dutch reader opens. `docs:check` catches them at source and `site:links` catches
  them again in the built output.
- **MDX evaluates `{…}` in prose as JavaScript.** These docs describe template variables like
  `{brand}` and `{provider}`, so writing one unquoted kills the build at *render* time with
  "provider is not defined" and a stack trace into a hashed chunk that names no page. Backtick it.

The production build is 100 % static (the Keystatic integration is only loaded when
`KEYSTATIC=1`), so deployment is any static host — or the Dockerfile here, which builds the
site and serves `dist/` with nginx:

```bash
docker build -f apps/site/Dockerfile -t schakl-site .   # run from the repo root
```

## Rules of the tree

- **nl and en docs mirror each other** — same file paths under `nl/docs/` and `en/docs/`
  (which is also how the CMS pairs a page's translations); `pnpm docs:check` fails on
  drift, exactly like `i18n:check` does for messages.
- File and directory names are neutral English (they are URLs); titles and prose are
  per-locale.
- A feature PR that changes behaviour updates its docs page in the same change (CLAUDE.md
  §9 definition of done).

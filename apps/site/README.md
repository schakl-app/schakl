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
Remote sign-in (edit from anywhere, drafts as branches) needs a small token-exchange
service (`sveltia-cms-auth`) and is decided together with the deploy target.

| What | Where | CMS surface |
| --- | --- | --- |
| Brand name, logo (+dark variant), favicon, **colors**, nav, footer | `src/data/settings/site.json` | Site-instellingen |
| Landing **blocks** (hero, feature grid, text+bullets, CTA — add/reorder freely; one entry, both locales) | `src/data/landing.json` | Landingspagina |
| Feature cards (NL+EN fields side by side) | `src/data/features/*.json` | Functie-kaarten |
| **Free-form pages** (MDX, own URL, site chrome; one entry, locale switcher) | `src/content/pages/{nl,en}/*` | Pagina's |
| Docs pages (one entry, locale switcher; paths pair the translations) | `src/content/docs/{nl,en}/docs/**` | Documentatie |

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
pnpm site cms          # same + Keystatic admin (KEYSTATIC=1)
pnpm site build        # static build to dist/ — this is what deploys
pnpm docs:check        # locale parity + module coverage for the docs (issue #136)
```

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

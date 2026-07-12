# @schakl/site — project website + docs

The public site for **schakl.** (issue #135): an Astro landing site with Starlight docs,
fully static output, Dutch (root) + English. This is the one place the schakl. brand lives —
the product itself is white-label.

## Everything is content

All user-visible content — brand, colors, logo, nav, footer, landing copy, feature cards,
and the docs pages themselves — lives as JSON/MDX in this package and is edited through
**Keystatic**:

```bash
pnpm site cms          # dev server with the CMS at http://localhost:4321/keystatic
```

| What | Where | CMS surface |
| --- | --- | --- |
| Brand name, logo (+dark variant), favicon, **colors**, nav, footer | `src/data/settings/site.json` | Site-instellingen |
| Landing **blocks** (hero, feature grid, text+bullets, CTA — add/reorder freely, per locale) | `src/data/landing/{nl,en}.json` | Landingspagina NL / EN |
| Feature cards | `src/data/features/*.json` | Functies |
| **Free-form pages** (MDX, own URL, site chrome) | `src/content/pages/{nl,en}/*` | Pagina's NL / EN |
| Docs pages | `src/content/docs/docs/**` (nl) · `src/content/docs/en/docs/**` (en) | Docs NL / EN |

Creating a page in *Pagina's (Nederlands)* with slug `prijzen` publishes `/prijzen/` on the
next build (English pages land under `/en/<slug>/`); link it by adding a nav item in
Site-instellingen. Don't use the slugs `docs` or `en` — those paths are taken. Landing
sections carry an optional *anker* so nav `#`-links keep working when blocks move.

The accent colors flow into the landing pages *and* the Starlight docs theme
(`astro.config.mjs` reads the settings at build time); the logo is referenced by path from
the same file. Changing them in the CMS and rebuilding restyles the whole site — no code.

Keystatic runs in `local` storage mode (edits the working tree → commit → rebuild). For a
hosted editing container later, switch `keystatic.config.ts` to GitHub storage mode.

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

- **nl and en docs mirror each other** — same file paths under `docs/` and `en/docs/`;
  `pnpm docs:check` fails on drift, exactly like `i18n:check` does for messages.
- File and directory names are neutral English (they are URLs); titles and prose are
  per-locale.
- A feature PR that changes behaviour updates its docs page in the same change (CLAUDE.md
  §9 definition of done).

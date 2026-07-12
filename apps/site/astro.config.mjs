// @ts-check
import { readFileSync } from 'node:fs';
import { defineConfig, passthroughImageService } from 'astro/config';
import starlight from '@astrojs/starlight';

// Site settings are content, not code: the Keystatic singleton at src/data/settings/site.json
// drives brand name, logo, favicon and colors. Editing it in the CMS and rebuilding restyles
// the whole site — including the Starlight docs theme below.
const settings = JSON.parse(
  readFileSync(new URL('./src/data/settings/site.json', import.meta.url), 'utf8'),
);

// Keystatic stores image paths as '/src/…' (repo-relative); Starlight wants './src/…'.
const logoSrc = settings.logo ? settings.logo.replace(/^\//, './') : undefined;
const logoDarkSrc = settings.logoDark ? settings.logoDark.replace(/^\//, './') : logoSrc;

// The Keystatic admin UI needs server routes + React, so it only exists in `pnpm site cms`
// (KEYSTATIC=1 astro dev). The production build stays 100 % static.
const cms = process.env.KEYSTATIC === '1';
const cmsIntegrations = cms
  ? [
      (await import('@astrojs/react')).default(),
      (await import('@keystatic/astro')).default(),
    ]
  : [];

export default defineConfig({
  site: settings.siteUrl || 'https://schakl.app',
  output: 'static',
  image: { service: passthroughImageService() },
  integrations: [
    starlight({
      title: settings.brandName,
      // The logo SVG carries the wordmark, so it replaces the text title; the dark
      // variant exists because an <img> cannot inherit currentColor from the page.
      logo: logoSrc
        ? { light: logoSrc, dark: logoDarkSrc, alt: settings.brandName, replacesTitle: true }
        : undefined,
      favicon: settings.favicon || '/favicon.svg',
      defaultLocale: 'root',
      locales: {
        root: { label: 'Nederlands', lang: 'nl' },
        en: { label: 'English', lang: 'en' },
      },
      social: settings.social?.github
        ? [{ icon: 'github', label: 'GitHub', href: settings.social.github }]
        : [],
      editLink: {
        baseUrl: 'https://github.com/schakl-app/schakl/edit/dev/apps/site/',
      },
      sidebar: [
        { slug: 'docs' },
        {
          label: 'Modules',
          autogenerate: { directory: 'docs/modules' },
        },
        {
          label: 'Beheer',
          translations: { en: 'Administration' },
          autogenerate: { directory: 'docs/admin' },
        },
      ],
      head: [
        {
          tag: 'style',
          content: `:root{--brand-accent:${settings.colors.accent};--brand-accent-dark:${settings.colors.accentDark};}`,
        },
      ],
      customCss: ['./src/styles/starlight.css'],
    }),
    ...cmsIntegrations,
  ],
});

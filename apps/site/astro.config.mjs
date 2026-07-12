// @ts-check
import { readFileSync } from 'node:fs';
import { defineConfig, passthroughImageService } from 'astro/config';
import starlight from '@astrojs/starlight';

// Site settings are content, not code: the Sveltia-managed file at src/data/settings/site.json
// drives brand name, logo, favicon and colors. Editing it in the CMS and rebuilding restyles
// the whole site — including the Starlight docs theme below.
const settings = JSON.parse(
  readFileSync(new URL('./src/data/settings/site.json', import.meta.url), 'utf8'),
);

// The CMS stores image paths as '/src/…' (repo-relative); Starlight wants './src/…'.
const logoSrc = settings.logo ? settings.logo.replace(/^\//, './') : undefined;
const logoDarkSrc = settings.logoDark ? settings.logoDark.replace(/^\//, './') : logoSrc;

export default defineConfig({
  site: settings.siteUrl || 'https://schakl.app',
  output: 'static',
  image: { service: passthroughImageService() },
  // The docs moved from a root-locale layout to symmetric /nl/ + /en/ folders when the CMS
  // gained per-entry locale switching (translations pair by file path).
  redirects: {
    '/docs': '/nl/docs',
  },
  integrations: [
    starlight({
      title: settings.brandName,
      // The logo SVG carries the wordmark, so it replaces the text title; the dark
      // variant exists because an <img> cannot inherit currentColor from the page.
      logo: logoSrc
        ? { light: logoSrc, dark: logoDarkSrc, alt: settings.brandName, replacesTitle: true }
        : undefined,
      favicon: settings.favicon || '/favicon.svg',
      defaultLocale: 'nl',
      locales: {
        nl: { label: 'Nederlands', lang: 'nl' },
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
  ],
});

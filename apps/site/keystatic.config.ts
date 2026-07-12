import { collection, config, fields, singleton } from '@keystatic/core';

// Everything user-visible on the site is edited here — brand (logo, colors, nav, footer),
// the landing pages per locale, the features grid, and the docs themselves. Content is
// stored as JSON/MDX in the repo: an edit is a commit, a commit is a rebuild.
//
// Run it with `pnpm site cms` → http://localhost:4321/keystatic
// Storage is `local` (edits the working tree). Switch to `{ kind: 'github', repo: … }`
// when the hosted editing container lands.

const localizedText = (label: string, multiline = false) =>
  fields.object(
    {
      nl: fields.text({ label: `${label} (NL)`, multiline }),
      en: fields.text({ label: `${label} (EN)`, multiline }),
    },
    { label },
  );

const cta = (label: string) =>
  fields.object(
    {
      label: fields.text({ label: 'Tekst' }),
      href: fields.text({ label: 'Link' }),
    },
    { label },
  );

const landingSchema = {
  hero: fields.object(
    {
      eyebrow: fields.text({ label: 'Eyebrow (regeltje boven de titel)' }),
      title: fields.text({ label: 'Titel' }),
      subtitle: fields.text({ label: 'Subtitel', multiline: true }),
      ctaPrimary: cta('Primaire knop'),
      ctaSecondary: cta('Secundaire knop'),
    },
    { label: 'Hero' },
  ),
  featuresTitle: fields.text({ label: 'Titel boven de functies' }),
  featuresIntro: fields.text({ label: 'Intro boven de functies', multiline: true }),
  selfhost: fields.object(
    {
      title: fields.text({ label: 'Titel' }),
      body: fields.text({ label: 'Tekst', multiline: true }),
      bullets: fields.array(fields.text({ label: 'Punt' }), {
        label: 'Punten',
        itemLabel: (props) => props.value ?? '',
      }),
    },
    { label: 'Zelf hosten-sectie' },
  ),
  docsCta: fields.object(
    {
      title: fields.text({ label: 'Titel' }),
      body: fields.text({ label: 'Tekst', multiline: true }),
      label: fields.text({ label: 'Knoptekst' }),
    },
    { label: 'Docs-callout onderaan' },
  ),
};

const docsCollection = (label: string, path: `${string}/**`) =>
  collection({
    label,
    path,
    slugField: 'title',
    format: { contentField: 'content' },
    entryLayout: 'content',
    columns: ['title'],
    schema: {
      title: fields.slug({ name: { label: 'Titel' } }),
      description: fields.text({
        label: 'Omschrijving (voor zoekmachines en linkkaarten)',
        multiline: true,
      }),
      content: fields.mdx({ label: 'Inhoud' }),
    },
  });

export default config({
  storage: { kind: 'local' },
  ui: { brand: { name: 'schakl.' } },
  singletons: {
    site: singleton({
      label: 'Site-instellingen',
      path: 'src/data/settings/site',
      format: { data: 'json' },
      schema: {
        brandName: fields.text({ label: 'Merknaam', defaultValue: 'schakl.' }),
        siteUrl: fields.text({ label: 'Site-URL' }),
        logo: fields.image({
          label: 'Logo',
          directory: 'src/assets/site',
          publicPath: '/src/assets/site/',
          validation: { isRequired: false },
        }),
        logoDark: fields.image({
          label: 'Logo — donker thema (optioneel; anders wordt het gewone logo gebruikt)',
          directory: 'src/assets/site',
          publicPath: '/src/assets/site/',
          validation: { isRequired: false },
        }),
        favicon: fields.image({
          label: 'Favicon (SVG aangeraden)',
          directory: 'public',
          publicPath: '/',
          validation: { isRequired: false },
        }),
        colors: fields.object(
          {
            accent: fields.text({
              label: 'Accentkleur — licht thema (hex, bijv. #4F46E5)',
            }),
            accentDark: fields.text({
              label: 'Accentkleur — donker thema (hex, bijv. #818CF8)',
            }),
          },
          { label: 'Kleuren' },
        ),
        social: fields.object(
          { github: fields.text({ label: 'GitHub-URL' }) },
          { label: 'Sociale links' },
        ),
        nav: fields.array(
          fields.object({
            href: fields.text({ label: 'Link' }),
            nl: fields.text({ label: 'Label (NL)' }),
            en: fields.text({ label: 'Label (EN)' }),
          }),
          { label: 'Hoofdnavigatie', itemLabel: (props) => props.fields.nl.value },
        ),
        footerLinks: fields.array(
          fields.object({
            href: fields.text({ label: 'Link' }),
            nl: fields.text({ label: 'Label (NL)' }),
            en: fields.text({ label: 'Label (EN)' }),
          }),
          { label: 'Footerlinks', itemLabel: (props) => props.fields.nl.value },
        ),
        footerNote: localizedText('Footertekst'),
      },
    }),
    landingNl: singleton({
      label: 'Landingspagina (Nederlands)',
      path: 'src/data/landing/nl',
      format: { data: 'json' },
      schema: landingSchema,
    }),
    landingEn: singleton({
      label: 'Landing page (English)',
      path: 'src/data/landing/en',
      format: { data: 'json' },
      schema: landingSchema,
    }),
  },
  collections: {
    features: collection({
      label: 'Functies (landingspagina)',
      path: 'src/data/features/*',
      slugField: 'name',
      format: { data: 'json' },
      columns: ['order'],
      schema: {
        name: fields.slug({ name: { label: 'Naam (intern)' } }),
        order: fields.integer({ label: 'Volgorde', defaultValue: 10 }),
        icon: fields.text({ label: 'Icoon (emoji)' }),
        title: localizedText('Titel'),
        description: localizedText('Omschrijving', true),
      },
    }),
    docsNl: docsCollection('Docs (Nederlands)', 'src/content/docs/docs/**'),
    docsEn: docsCollection('Docs (English)', 'src/content/docs/en/docs/**'),
  },
});

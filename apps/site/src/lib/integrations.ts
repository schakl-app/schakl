// The integrations index: one card per connected service, grouped into categories.
//
// Same split as src/lib/features.ts — the *cards* are content (src/data/integrations/*.json,
// editable in the CMS), the *grouping* is presentation and lives here in code. A category is
// not a field on the card: putting it in the JSON would let the CMS invent a fourteenth
// category with one item in it, and the page's shape is a design decision, not an editorial one.
//
// `status` is the one thing this page exists to be honest about, and the line it draws is
// between what you can connect and what you cannot:
//
//   live    — it ships, it is connectable, there is a settings screen and a guide.
//   beta    — reserved. No card carries it today; it stays in the vocabulary because the CMS
//             offers it and because a future integration may want to ship behind it.
//   planned — on the roadmap, connects to nothing at all. Stripe, Adyen, Exact Online and
//             outbound event webhooks are the four, and each carries a `roadmapNote` saying
//             what exists instead. This is the value that must never be softened: a card
//             reading "Available" over an integration with no screen behind it is a promise
//             the first agency to try it discovers is false.
//
// Every card carries its badge, and roadmap cards are never mixed in among the working ones —
// `groupedIntegrations` splits them, and site-content-check.mjs enforces the two invariants a
// roadmap card has (it explains itself; it links to no guide that cannot exist).
export type Locale = 'nl' | 'en';
export type IntegrationStatus = 'live' | 'beta' | 'planned';

const modules = import.meta.glob('../data/integrations/*.json', { eager: true });

export const integrations = Object.values(modules)
  .map((m: any) => m.default ?? m)
  .sort((a: any, b: any) => (a.order ?? 0) - (b.order ?? 0));

export const integrationBySlug: Record<string, any> = Object.fromEntries(
  integrations.map((i: any) => [i.slug, i]),
);

export interface IntegrationCategory {
  key: string;
  nl: string;
  en: string;
  lucide: string;
  intro: { nl: string; en: string };
}

export const categories: IntegrationCategory[] = [
  {
    key: 'payments',
    nl: 'Betalen',
    en: 'Payments',
    lucide: 'creditCard',
    intro: {
      nl: 'Laat klanten je factuur online betalen; de betaling landt als betaalregel op de factuur zelf.',
      en: 'Let clients pay your invoice online; the payment lands as a payment line on the invoice itself.',
    },
  },
  {
    key: 'accounting',
    nl: 'Boekhouding',
    en: 'Accounting',
    lucide: 'ledger',
    intro: {
      nl: 'Je facturen doorzetten naar het pakket waar je accountant in werkt.',
      en: 'Push your invoices through to the package your accountant works in.',
    },
  },
  {
    key: 'domains',
    nl: 'Domeinen & DNS',
    en: 'Domains & DNS',
    lucide: 'globe',
    intro: {
      nl: 'Het register weet wie een domein betaalt, de zone weet waar het heen wijst. schakl leest allebei.',
      en: 'The registrar knows who pays for a domain, the zone knows where it points. schakl reads both.',
    },
  },
  {
    key: 'websites',
    nl: 'Websites & monitoring',
    en: 'Websites & monitoring',
    lucide: 'server',
    intro: {
      nl: 'Wat er op de site van een klant draait, en of hij overeind staat. Allebei hangen ze aan de website, niet aan het domein.',
      en: "What runs on a client's site, and whether it is up. Both hang off the website, not off the domain.",
    },
  },
  {
    key: 'google',
    nl: 'Google Workspace',
    en: 'Google Workspace',
    lucide: 'mail',
    intro: {
      nl: 'Agenda, Drive, Gmail en Contacten, per organisatie gekoppeld met je eigen OAuth-gegevens.',
      en: 'Calendar, Drive, Gmail and Contacts, connected per organisation with your own OAuth credentials.',
    },
  },
  {
    key: 'marketing',
    nl: 'Marketing & analytics',
    en: 'Marketing & analytics',
    lucide: 'chart',
    intro: {
      nl: 'De cijfers waarmee je een klant laat zien wat het werk heeft opgeleverd.',
      en: 'The numbers you use to show a client what the work delivered.',
    },
  },
  {
    key: 'ai',
    nl: 'AI & assistenten',
    en: 'AI & assistants',
    lucide: 'bot',
    intro: {
      nl: 'Jouw sleutel, jouw aanbieder, jouw rechten. Zonder ingestelde aanbieder is er nergens een AI-knop.',
      en: 'Your key, your provider, your permissions. With no provider configured there is no AI button anywhere.',
    },
  },
  {
    key: 'identity',
    nl: 'Inloggen & identiteit',
    en: 'Login & identity',
    lucide: 'shield',
    intro: {
      nl: 'Inloggen met het account dat je bureau al heeft, met tweestapsverificatie erbovenop.',
      en: 'Sign in with the account your agency already has, with two-factor verification on top.',
    },
  },
  {
    key: 'infrastructure',
    nl: 'Opslag & verzending',
    en: 'Storage & delivery',
    lucide: 'database',
    intro: {
      nl: 'Waar je bestanden staan en langs welke server je e-mail de deur uitgaat.',
      en: 'Where your files live and which server your mail leaves through.',
    },
  },
  {
    key: 'automation',
    nl: 'Automatisering & API',
    en: 'Automation & API',
    lucide: 'workflow',
    intro: {
      nl: 'Alles wat de app kan, kan een script ook: één API, getypt, met rechten per sleutel.',
      en: 'Anything the app can do, a script can too: one API, typed, with permissions per key.',
    },
  },
];

export function groupedIntegrations() {
  return categories
    .map((c) => ({
      ...c,
      items: integrations.filter((i: any) => i.category === c.key),
      // `beta` sits with the working ones — it *is* connectable — and carries its own badge
      // there. Only `planned` is separated out, because that is the one that connects to
      // nothing at all.
      ready: integrations.filter(
        (i: any) => i.category === c.key && (i.status === 'live' || i.status === 'beta'),
      ),
      planned: integrations.filter((i: any) => i.category === c.key && i.status === 'planned'),
    }))
    .filter((c) => c.items.length > 0);
}

export const statusLabel: Record<IntegrationStatus, { nl: string; en: string }> = {
  live: { nl: 'Beschikbaar', en: 'Available' },
  beta: { nl: 'Bèta', en: 'Beta' },
  planned: { nl: 'Op de roadmap', en: 'On the roadmap' },
};

// Spelled out wherever a beta card is drawn, so the badge is never the only warning.
export const betaNote = {
  nl: 'Gebouwd op de officiële API-documentatie van de leverancier en getest tegen een nabootsing daarvan. Nog niet tegen een echte account van die leverancier gedraaid: reken op een proefrun voordat je hem op klanten loslaat.',
  en: "Built on the provider's official API documentation and tested against a stand-in cut from it. Not yet run against a real account at that provider: plan a rehearsal before you point clients at it.",
};

export const counts = () => ({
  live: integrations.filter((i: any) => i.status === 'live').length,
  beta: integrations.filter((i: any) => i.status === 'beta').length,
  ready: integrations.filter((i: any) => i.status === 'live' || i.status === 'beta').length,
  planned: integrations.filter((i: any) => i.status === 'planned').length,
  categories: groupedIntegrations().length,
});

// One place to load the feature cards and the grouping used by the mega-menu and the
// /features/ overview page. The cards themselves live in src/data/features/*.json (CMS);
// the grouping + short menu taglines are presentation, so they live here in code.
export type Locale = 'nl' | 'en';

const modules = import.meta.glob('../data/features/*.json', { eager: true });
export const features = Object.values(modules)
  .map((m: any) => m.default ?? m)
  .sort((a: any, b: any) => a.order - b.order);

export const featureBySlug: Record<string, any> = Object.fromEntries(
  features.map((f: any) => [f.slug, f]),
);

// Very short taglines for the mega-menu (the card descriptions are a touch long there).
export const taglines: Record<string, { nl: string; en: string }> = {
  dashboard: { nl: 'Jouw bord, elke ochtend', en: 'Your board, every morning' },
  companies: { nl: 'De spil van alles', en: 'The hub of it all' },
  contacts: { nl: 'De mensen bij de klant', en: 'The people at the client' },
  projects: { nl: 'Budgetten die kloppen', en: 'Budgets that add up' },
  // Kept short on purpose: the mega-menu clips a tagline that does not fit its column, and the
  // panel went from three columns to four when the growth group was added.
  tasks: { nl: 'Borden en checklists', en: 'Boards and checklists' },
  calendar: { nl: 'Alle feeds op één plek', en: 'Every feed in one place' },
  time: { nl: 'Timer, weekstaat, facturabel', en: 'Timer, timesheet, billable' },
  interactions: { nl: 'Alles op één tijdlijn', en: 'All on one timeline' },
  subscriptions: { nl: 'Terugkerende diensten', en: 'Recurring services' },
  invoicing: { nl: 'Facturen, offertes, betaald', en: 'Invoices, quotes, paid' },
  assets: { nl: 'Domeinen, sites, hosting', en: 'Domains, sites, hosting' },
  leave: { nl: 'Verlof, live berekend', en: 'Leave, computed live' },
  hr: { nl: 'Contracten en roosters', en: 'Contracts and schedules' },
  marketing: { nl: 'De cijfers van de klant', en: "The client's numbers" },
  reporting: { nl: 'Elke maand een verhaal', en: 'A story every month' },
  portal: { nl: 'De klant kijkt mee', en: 'The client looks in' },
  customfields: { nl: 'Je eigen velden', en: 'Your own fields' },
  roles: { nl: 'Wie wat mag', en: 'Who may do what' },
  activity: { nl: 'Een spoor dat blijft', en: 'A trail that stays' },
  impex: { nl: 'Erin en eruit, als sheet', en: 'In and out, as a sheet' },
  notifications: { nl: 'Weten wanneer het telt', en: 'Know when it matters' },
  automation: { nl: 'Als dit, dan dat', en: 'If this, then that' },
  ai: { nl: 'Jouw sleutel, jouw model', en: 'Your key, your model' },
  whitelabel: { nl: 'Jouw merk, jouw kleur', en: 'Your brand, your colour' },
  integrations: { nl: 'Mollie, Cloudflare, Google', en: 'Mollie, Cloudflare, Google' },
};

export interface FeatureGroup {
  key: string;
  nl: string;
  en: string;
  intro: { nl: string; en: string };
  slugs: string[];
}

export const groups: FeatureGroup[] = [
  {
    key: 'work',
    nl: 'Klantwerk',
    en: 'Client work',
    intro: {
      nl: 'De klant is het middelpunt; hieraan hangt het werk dat je factureert.',
      en: 'The client is the centre; the billable work hangs off it.',
    },
    slugs: ['dashboard', 'companies', 'contacts', 'projects', 'tasks', 'calendar', 'time'],
  },
  {
    key: 'agency',
    nl: 'Bureau draaien',
    en: 'Run the agency',
    intro: {
      nl: 'Contact, diensten, geld, assets en mensen: de dagelijkse bedrijfsvoering.',
      en: 'Contact, services, money, assets and people: the day-to-day of the shop.',
    },
    slugs: ['interactions', 'subscriptions', 'invoicing', 'assets', 'leave', 'hr'],
  },
  {
    key: 'growth',
    nl: 'Groeien & laten zien',
    en: 'Grow and show',
    intro: {
      nl: 'De cijfers van de klant, het verhaal eromheen, en een plek waar de klant zelf kijkt.',
      en: "The client's numbers, the story around them, and a place the client can look for themselves.",
    },
    slugs: ['marketing', 'reporting', 'portal'],
  },
  {
    key: 'platform',
    nl: 'Platform & koppelingen',
    en: 'Platform & integrations',
    intro: {
      nl: 'Cross-cutting fundamenten en de manieren om schakl. te verbinden.',
      en: 'Cross-cutting foundations and the ways to connect schakl.',
    },
    slugs: [
      'customfields',
      'roles',
      'activity',
      'impex',
      'notifications',
      'automation',
      'ai',
      'whitelabel',
      'integrations',
    ],
  },
];

export function grouped() {
  return groups.map((g) => ({
    ...g,
    items: g.slugs.map((s) => featureBySlug[s]).filter(Boolean),
  }));
}

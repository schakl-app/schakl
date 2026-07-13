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
  companies: { nl: 'De spil van alles', en: 'The hub of it all' },
  contacts: { nl: 'De mensen bij de klant', en: 'The people at the client' },
  projects: { nl: 'Budgetten die kloppen', en: 'Budgets that add up' },
  tasks: { nl: 'Borden, checklists, sjablonen', en: 'Boards, checklists, templates' },
  time: { nl: 'Timer, weekstaat, facturabel', en: 'Timer, timesheet, billable' },
  interactions: { nl: 'Alles op één tijdlijn', en: 'All on one timeline' },
  subscriptions: { nl: 'Terugkerende diensten', en: 'Recurring services' },
  assets: { nl: 'Domeinen, sites, hosting', en: 'Domains, sites, hosting' },
  leave: { nl: 'Verlof, live berekend', en: 'Leave, computed live' },
  customfields: { nl: 'Je eigen velden', en: 'Your own fields' },
  roles: { nl: 'Wie wat mag', en: 'Who may do what' },
  activity: { nl: 'Een spoor dat blijft', en: 'A trail that stays' },
  whitelabel: { nl: 'Jouw merk, jouw kleur', en: 'Your brand, your color' },
  integrations: { nl: 'Google, API en AI', en: 'Google, API and AI' },
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
    slugs: ['companies', 'contacts', 'projects', 'tasks', 'time'],
  },
  {
    key: 'agency',
    nl: 'Bureau draaien',
    en: 'Run the agency',
    intro: {
      nl: 'Contact, diensten, assets en mensen: de dagelijkse bedrijfsvoering.',
      en: 'Contact, services, assets and people: the day-to-day of the shop.',
    },
    slugs: ['interactions', 'subscriptions', 'assets', 'leave'],
  },
  {
    key: 'platform',
    nl: 'Platform & koppelingen',
    en: 'Platform & integrations',
    intro: {
      nl: 'Cross-cutting fundamenten en de manieren om schakl te verbinden.',
      en: 'Cross-cutting foundations and the ways to connect schakl.',
    },
    slugs: ['customfields', 'roles', 'activity', 'whitelabel', 'integrations'],
  },
];

export function grouped() {
  return groups.map((g) => ({
    ...g,
    items: g.slugs.map((s) => featureBySlug[s]).filter(Boolean),
  }));
}

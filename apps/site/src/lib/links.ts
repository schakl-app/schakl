// One place for locale-aware links, used by the shell and the landing blocks.
// English is the default locale and lives at the root ('/'); Dutch is prefixed at '/nl'.
// CMS content writes locale-neutral hrefs ('/docs/', '/#features'); this maps them:
// docs links get their locale prefix (the docs tree is symmetric /nl/ + /en/), and other
// internal links get the '/nl' prefix on Dutch pages while English stays at the root.
export const localizeHref = (locale: 'nl' | 'en', h: string): string => {
  if (!h.startsWith('/')) return h;
  if (h === '/docs' || h.startsWith('/docs/')) return `/${locale}${h}`;
  if (locale === 'nl' && !h.startsWith('/nl')) return `/nl${h}`;
  return h;
};

// The same marketing page in the other locale, given the current path. English lives at the
// root, Dutch under /nl — so this strips or adds the /nl prefix. Used by the language switcher.
export const altHref = (path: string, target: 'nl' | 'en'): string => {
  const clean = path.replace(/\/+$/, '') || '/'; // drop trailing slash for logic
  const isNl = clean === '/nl' || clean.startsWith('/nl/');
  const root = isNl ? clean.slice(3) || '/' : clean; // '/nl/x' -> '/x', '/nl' -> '/'
  const p = target === 'nl' ? (root === '/' ? '/nl' : `/nl${root}`) : root;
  return p === '/' ? '/' : `${p}/`;
};

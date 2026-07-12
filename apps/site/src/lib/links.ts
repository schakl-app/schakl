// One place for locale-aware links, used by the shell and the landing blocks.
// CMS content writes locale-neutral hrefs ('/docs/', '/#features'); this maps them:
// docs links get their locale prefix (the docs tree is symmetric /nl/ + /en/), and
// other internal links get /en/ on the English pages.
export const localizeHref = (locale: 'nl' | 'en', h: string): string => {
  if (!h.startsWith('/')) return h;
  if (h === '/docs' || h.startsWith('/docs/')) return `/${locale}${h}`;
  if (locale === 'en' && !h.startsWith('/en')) return `/en${h}`;
  return h;
};

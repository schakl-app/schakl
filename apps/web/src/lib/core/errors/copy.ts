/**
 * What an error page *says*, per HTTP status (CLAUDE.md §8, Golden Rule 2).
 *
 * There are three renderers of the same idea and they cannot share markup: `+error.svelte`
 * renders inside the running app, `standalone.server.ts` renders a self-contained document for
 * the moment the API is unreachable, and the API renders its own Python twin for the moment the
 * SSR app is the thing that is down (`app/core/errorpage.py`). Different situations, different
 * dependencies, deliberately different code.
 *
 * The *wording* is the part that must not drift, so it lives here as message keys and nowhere
 * else. Dependency-free on purpose: this module is imported by a Svelte component, by a
 * server-only string renderer, and by `node --test` directly.
 */

export interface ErrorCopy {
  /** i18n key for the headline. */
  titleKey: string;
  /** i18n key for the sentence under it. */
  bodyKey: string;
  /**
   * The page is worth *reloading* rather than navigating away from: the thing that failed is
   * ours and transient (a restarting API, a gateway blip), so "probeer opnieuw" is real advice.
   * A 404 or a 403 will answer the same way however many times it is asked.
   */
  retryable: boolean;
}

const NOT_FOUND: ErrorCopy = {
  titleKey: "errors.page.not_found.title",
  bodyKey: "errors.page.not_found.body",
  retryable: false,
};
const UNAUTHORIZED: ErrorCopy = {
  titleKey: "errors.page.unauthorized.title",
  bodyKey: "errors.page.unauthorized.body",
  retryable: false,
};
const FORBIDDEN: ErrorCopy = {
  titleKey: "errors.page.forbidden.title",
  bodyKey: "errors.page.forbidden.body",
  retryable: false,
};
const RATE_LIMITED: ErrorCopy = {
  titleKey: "errors.page.rate_limited.title",
  bodyKey: "errors.page.rate_limited.body",
  retryable: true,
};
const GENERIC: ErrorCopy = {
  titleKey: "errors.page.generic.title",
  bodyKey: "errors.page.generic.body",
  retryable: false,
};
const SERVER: ErrorCopy = {
  titleKey: "errors.page.server.title",
  bodyKey: "errors.page.server.body",
  retryable: true,
};
/**
 * "Briefly unavailable", not "broken". This is what a rolling redeploy looks like from the
 * outside (docs/DEPLOY.md) and what the visitor should be told: waiting works, and there is
 * nothing for them to fix. Saying "er ging iets mis" over a planned two-minute rollover sends
 * an agency's client to the phone.
 */
const UNAVAILABLE: ErrorCopy = {
  titleKey: "errors.page.unavailable.title",
  bodyKey: "errors.page.unavailable.body",
  retryable: true,
};

const BY_STATUS: Record<number, ErrorCopy> = {
  401: UNAUTHORIZED,
  403: FORBIDDEN,
  404: NOT_FOUND,
  408: UNAVAILABLE,
  429: RATE_LIMITED,
  502: UNAVAILABLE,
  503: UNAVAILABLE,
  504: UNAVAILABLE,
};

/**
 * The copy for `status`. An unknown 4xx is the visitor's request being wrong in some way we have
 * no specific sentence for; an unknown 5xx is ours. Anything outside both — including a status
 * parsed out of a URL segment by the edge-error route — falls back to the generic sentence
 * rather than inventing one.
 */
export function errorCopy(status: number): ErrorCopy {
  const known = BY_STATUS[status];
  if (known) return known;
  if (status >= 500 && status <= 599) return SERVER;
  if (status >= 400 && status <= 499) return GENERIC;
  return GENERIC;
}

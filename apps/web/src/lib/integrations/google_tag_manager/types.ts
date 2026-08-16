/**
 * The shapes the Tag Manager search hands back, in one place because two components and a proxy
 * read them. Mirrors `GtmAvailableContainer` / `GtmPickerRead` in the API's own schemas.
 */

/** One container the caller's Google grant can reach, as the search offers it. */
export interface GtmSearchHit {
  gtm_account_id: string;
  account_name: string;
  gtm_container_id: string;
  public_id: string;
  name: string;
  usage_context: string[];
  already_linked: boolean;
}

/**
 * What the search answered.
 *
 * `accounts_total` / `accounts_read` are the half that stops a short list from reading as a
 * complete one: Tag Manager's quota is per user per minute, so a search opens only the accounts
 * it matched, and "8 van 44" is what turns an empty result into an instruction.
 */
export interface GtmSearchResponse {
  query?: string;
  containers?: GtmSearchHit[];
  /** i18n keys for anything that limited the answer — a cap, a refusing account, the quota. */
  warnings?: string[];
  accounts_total?: number;
  accounts_read?: number;
  /** An error key the picker can act on; `errors.gtm_not_configured` is cured by a reconnect. */
  error?: string;
}

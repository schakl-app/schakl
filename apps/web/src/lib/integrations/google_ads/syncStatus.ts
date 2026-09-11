import { t } from "$lib/core/i18n";

/**
 * The nightly mirror's stored failure, as a sentence.
 *
 * `last_sync_error` holds one of two kinds of text: an i18n key (an `AppError` the sync loop
 * caught — an unreadable developer token) or Google's own prose (`describe_failure`). Only the
 * first is translated; a key that resolves to itself is prose and is printed as it came.
 */
export function syncErrorText(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const translated = t(raw);
  return translated === raw ? raw : translated;
}

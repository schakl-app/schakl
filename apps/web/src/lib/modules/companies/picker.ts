/**
 * What a client picker offers, and what each row says about itself.
 *
 * Every picker in the app that points at a client used to be `companies.map(c => ({value, label}))`
 * — twenty-odd copies of a mapping that could not tell an archived client from a live one. A new
 * domain, a new invoice, a new time entry: all of them offered the clients the agency stopped
 * working for three years ago, in alphabetical order, indistinguishable from the ones it works
 * for today.
 *
 * The lifecycle rule is the client list's own (#329): only `archived` is out of the working book
 * of business — a lead is being chased and an offboarding client is still being invoiced, so both
 * stay on offer and merely say which they are.
 */

import {
  splitLifecycle,
  type LifecycleSplit,
  type LifecycleVocabulary,
  type StatusedOption,
} from "$lib/core/picker";
import { t } from "$lib/core/i18n";

/** The shape every client lookup already returns; extra fields are ignored. */
export interface PickerCompany {
  id: string;
  name: string;
  status?: string | null;
}

/** Only the archive. See the module docstring — the other four statuses are live relationships. */
export const COMPANY_RETIRED_STATUSES = ["archived"] as const;

/** `active` is the unremarkable one: a pill on every other row would say nothing. */
const QUIET = ["active"] as const;

export interface CompanyPickerOptions {
  /** The client(s) currently held by the field(s) — always offered, archived or not. */
  selectedId?: string | readonly (string | null | undefined)[];
  /** An extra line per row (a client number, a city). The status is prefixed to it. */
  hint?: (company: PickerCompany) => string | undefined;
}

/**
 * The whole rule in one value, for a *core* picker that must not import this module
 * (`PartyPicker`). A function rather than a constant because two of its four fields are
 * translations, and a constant would freeze them at import time.
 */
export function companyLifecycle(): LifecycleVocabulary {
  return { ...rules(), archivedLabel: companyArchivedLabel() };
}

function rules() {
  return {
    retired: COMPANY_RETIRED_STATUSES,
    quiet: QUIET,
    statusLabel: (status: string) => t(`companies.status.${status}`),
  };
}

export function splitCompanyOptions(
  companies: readonly PickerCompany[],
  { selectedId = [], hint }: CompanyPickerOptions = {},
): LifecycleSplit {
  const options: StatusedOption[] = companies.map((company) => ({
    value: company.id,
    label: company.name,
    status: company.status ?? null,
    hint: hint?.(company),
  }));
  return splitLifecycle(options, { ...rules(), selectedId });
}

/** The heading `Combobox` draws above the search-only rows. */
export function companyArchivedLabel(): string {
  return t("companies.picker.archived");
}

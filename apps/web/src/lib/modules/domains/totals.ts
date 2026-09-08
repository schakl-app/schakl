/**
 * What a set of domains adds up to, said in one line (#298's other half).
 *
 * The API keeps two sums apart — what the renewal cron will bill, and what the domains set
 * *not invoiced* would cost — because an agency's own names are parked on its own company
 * record and never billed, while still costing their renewal every year. A single "yearly
 * total" either counts those as revenue or makes them vanish. So the sentence names each half
 * only where it is non-zero, and the domains with no price in force are counted rather than
 * summed as zero (docs/UX.md: an honest dash, never a reassuring zero).
 *
 * One function for the client card, the register's section headings and its footer, so three
 * surfaces describing one portfolio cannot word it three ways.
 */
import type { components } from "$lib/core/api/schema";
import { fmtMoney } from "$lib/core/format";
import { t } from "$lib/core/i18n";

export type DomainTotals = components["schemas"]["DomainTotals"];

/** The parts of the sentence, in reading order; the caller joins them with its own separator. */
export function totalsParts(totals: DomainTotals, { count = true } = {}): string[] {
  const parts: string[] = [];
  if (count) {
    parts.push(
      totals.count === 1
        ? t("domains.totals.count_one")
        : t("domains.totals.count", { count: totals.count }),
    );
  }
  if (totals.invoiced_count > 0) {
    parts.push(t("domains.totals.invoiced", { amount: fmtMoney(Number(totals.invoiced_yearly)) }));
  }
  if (totals.uninvoiced_count > 0) {
    // Priced only where a price exists: "niet gefactureerd (€ 0,00)" over an unpriced portfolio
    // would claim the domains cost nothing.
    const priced = Number(totals.uninvoiced_yearly) > 0;
    const params = {
      count: totals.uninvoiced_count,
      amount: fmtMoney(Number(totals.uninvoiced_yearly)),
    };
    parts.push(
      totals.uninvoiced_count === 1
        ? t(
            priced ? "domains.totals.uninvoiced_priced_one" : "domains.totals.uninvoiced_one",
            params,
          )
        : t(priced ? "domains.totals.uninvoiced_priced" : "domains.totals.uninvoiced", params),
    );
  }
  if (totals.unpriced_count > 0) {
    parts.push(
      totals.unpriced_count === 1
        ? t("domains.totals.unpriced_one")
        : t("domains.totals.unpriced", { count: totals.unpriced_count }),
    );
  }
  return parts;
}

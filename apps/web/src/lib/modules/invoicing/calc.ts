/**
 * Client-side mirror of the API's totals math — **display only** (the live preview under
 * the line editor). The server recomputes on every save and is the authority (#48); if the
 * two ever disagree, the saved document shows the server's numbers.
 *
 * Mirrors `apps/api/app/modules/invoicing/calc.py`: per-rate-group tax, rounded half-up
 * once per group; exempt/reverse-charge groups charge nothing; inclusive prices peel the
 * tax out of the group gross.
 */
import type { TaxRate } from "./types";

export interface EditableLine {
  description: string;
  quantity: string;
  unit: string;
  unit_price: string;
  tax_rate_id: string;
  /** The unbilled time entry this line was prefilled from (new-invoice form). Posted so the
   *  API bills that entry; absent on hand-typed lines. */
  time_entry_id?: string;
  /** Client-only: this line was auto-added from unbilled time, so a client change may replace
   *  it — hand-typed lines (falsy) are never clobbered. Never serialized to the API. */
  auto?: boolean;
}

export interface PreviewGroup {
  ratePct: number;
  category: string;
  name: string;
  base: number;
  tax: number;
}

export interface PreviewTotals {
  subtotal: number;
  taxTotal: number;
  total: number;
  groups: PreviewGroup[];
}

const UNTAXED = new Set(["exempt", "reverse_charge"]);

/** Half-up to cents, matching Python's ROUND_HALF_UP (JS `Math.round` half-ups positives). */
function cents(value: number): number {
  const sign = value < 0 ? -1 : 1;
  return (sign * Math.round(Math.abs(value) * 100 + 1e-9)) / 100;
}

export function computePreview(
  lines: EditableLine[],
  rates: TaxRate[],
  pricesIncludeTax: boolean,
  rateLabel: (rate: TaxRate | undefined) => string,
): PreviewTotals {
  const byId = new Map(rates.map((r) => [r.id, r]));
  const buckets = new Map<string, PreviewGroup & { amount: number }>();
  for (const line of lines) {
    const rate = line.tax_rate_id ? byId.get(line.tax_rate_id) : undefined;
    const pct = rate ? Number(rate.rate) : 0;
    const category = rate?.category ?? "standard";
    const key = `${pct}|${category}`;
    const amount = cents(Number(line.quantity || 0) * Number(line.unit_price || 0));
    const bucket = buckets.get(key) ?? {
      ratePct: pct,
      category,
      name: rateLabel(rate),
      base: 0,
      tax: 0,
      amount: 0,
    };
    bucket.amount = cents(bucket.amount + amount);
    buckets.set(key, bucket);
  }
  const groups: PreviewGroup[] = [];
  for (const bucket of [...buckets.values()].sort(
    (a, b) => b.ratePct - a.ratePct || a.category.localeCompare(b.category),
  )) {
    const taxable = !UNTAXED.has(bucket.category) && bucket.ratePct !== 0;
    if (!taxable) {
      groups.push({ ...bucket, base: bucket.amount, tax: 0 });
      continue;
    }
    const factor = 1 + bucket.ratePct / 100;
    const base = pricesIncludeTax ? cents(bucket.amount / factor) : bucket.amount;
    const tax = pricesIncludeTax
      ? cents(bucket.amount - base)
      : cents(base * (bucket.ratePct / 100));
    groups.push({ ...bucket, base, tax });
  }
  const subtotal = cents(groups.reduce((sum, g) => sum + g.base, 0));
  const taxTotal = cents(groups.reduce((sum, g) => sum + g.tax, 0));
  return { subtotal, taxTotal, total: cents(subtotal + taxTotal), groups };
}

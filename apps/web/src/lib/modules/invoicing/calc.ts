/**
 * Client-side mirror of the API's totals math — **display only** (the live preview under
 * the line editor). The server recomputes on every save and is the authority (#48); if the
 * two ever disagree, the saved document shows the server's numbers.
 *
 * Mirrors `apps/api/app/modules/invoicing/calc.py`: per-rate-group tax, rounded half-up
 * once per group; exempt/reverse-charge groups charge nothing; inclusive prices peel the
 * tax out of the group gross.
 */
import type { LineKind, TaxRate } from "./types";

export interface EditableLine {
  description: string;
  /** Hours / subscription / product — which section this line lives in, and how the
   *  document groups it. Decided by the section it was created in, not by a per-row picker. */
  line_kind: LineKind;
  quantity: string;
  unit: string;
  unit_price: string;
  tax_rate_id: string;
  /** The unbilled time entries this line bills. A **list**: a line picked per entry carries
   *  one, a line built by `from-time` grouping carries the whole project's worth. Posted and
   *  echoed back on read, so re-saving a draft cannot make it forget what it billed. */
  time_entry_ids?: string[];
  /** The agreement (or domain) and the period a recurring line bills. Posted so the API
   *  claims that period and the cron never invoices it a second time — and read back for the
   *  same reason: a save that dropped the claim handed the month straight back to the cron. */
  subscription_id?: string;
  domain_id?: string;
  period_start?: string;
  period_end?: string;
  /** Client-only: a stable key for the `{#each}`. Keying by array index breaks reordering and
   *  makes every row below a deletion re-render with the wrong state. Never sent. */
  key?: string;
}

/** A fresh client-side key for a line. Nothing about it reaches the API. */
export function lineKey(): string {
  return `l${Math.random().toString(36).slice(2, 10)}`;
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

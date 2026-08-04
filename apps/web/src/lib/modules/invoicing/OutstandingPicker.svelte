<script lang="ts">
  /**
   * "What does this client still have to be invoiced for?" — one section's worth, picked.
   *
   * Replaces the old silent prefill, which dropped **every** unbilled hour onto a fresh
   * invoice the moment you chose a client and left you deleting lines. Nothing is added
   * behind your back now: the section shows a count, you open this, you tick what belongs on
   * this invoice.
   *
   * Rows are already-priced offers, not raw records: the hours carry the rate chain the API
   * resolved (#226) and a period carries the price valid **at its own boundary**, so a line
   * added here bills exactly what the cron would have billed. Periods a document already
   * holds are listed, disabled and labelled rather than hidden — "did I invoice March?" is
   * the question this dialog exists to answer, and answering it by omission produces a
   * duplicate a week later.
   */
  import { fmtDayMonthYear, fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import { docMoney } from "./types";
  import type { BillableDomain, BillableSubscription, LineKind, UnbilledEntry } from "./types";

  /** One tickable thing. Whatever the section, the dialog only ever offers priced rows. */
  export interface Offer {
    id: string;
    label: string;
    /** The second line under the label: project, period, agreement name. */
    hint: string;
    quantity: string;
    unitPrice: string;
    amount: number;
    /** Why this row cannot be ticked, as a rendered string; "" when it can. */
    blocked: string;
  }

  let {
    open = $bindable(false),
    kind,
    hours,
    subscriptions = [],
    domains = [],
    currency,
    locale,
    loading = false,
    onadd,
  }: {
    open?: boolean;
    kind: LineKind;
    hours?: { entries: UnbilledEntry[]; truncated?: boolean; total_count?: number } | null;
    subscriptions?: BillableSubscription[];
    domains?: BillableDomain[];
    currency: string;
    locale: string;
    loading?: boolean;
    /** The picked offer ids, in the order they were listed. */
    onadd: (ids: string[]) => void;
  } = $props();

  const money = (value: number) => docMoney(value, currency, locale);

  /** Hours: one row per entry, priced at the rate the API resolved for its logger. */
  const hourOffers = $derived.by((): Offer[] =>
    (hours?.entries ?? []).map((entry) => {
      const quantity = (entry.minutes / 60).toFixed(2);
      return {
        id: `t:${entry.id}`,
        label: entry.description?.trim() || entry.project_name || t("invoicing.new.time_line_fallback"),
        hint: [fmtDayMonthYear(entry.started_at.slice(0, 10)), entry.project_name, entry.user_name]
          .filter(Boolean)
          .join(" · "),
        quantity,
        unitPrice: String(Number(entry.rate)),
        amount: Number(quantity) * Number(entry.rate),
        blocked: "",
      };
    }),
  );

  /** Recurring: one row per (agreement, period) — the unit a claim is made on. An agreement
   *  that cannot name a period cannot carry a claim, so it is surfaced as a reason, never as
   *  a tickable row that would fail on save. */
  function periodOffers(
    rows: (BillableSubscription | BillableDomain)[],
    prefix: string,
  ): Offer[] {
    const out: Offer[] = [];
    for (const row of rows) {
      // A domain the agency does not invoice (#298): labelled, **not** blocked. The renewal
      // cron skips it; a human billing one by hand is a different act and stays allowed.
      const notInvoiced = "invoiceable" in row && row.invoiceable === false;
      if (row.no_cycle) {
        out.push({
          id: `${prefix}:${row.id}:none`,
          label: row.name,
          hint: t("invoicing.outstanding.no_cycle"),
          quantity: "1",
          unitPrice: "0",
          amount: 0,
          blocked: t("invoicing.outstanding.no_cycle"),
        });
        continue;
      }
      for (const period of row.periods ?? []) {
        // The picker hint is transient UI, so it reads in the viewer's locale and is
        // year-aware; the *stored* line text stays dd-mm-jjjj (`periodText`).
        const span = fmtPeriod(period.period_start ?? period.period_end, period.period_end);
        out.push({
          id: `${prefix}:${row.id}:${period.period_end}`,
          label: row.name,
          hint: [
            span,
            period.future ? t("invoicing.outstanding.future") : "",
            notInvoiced ? t("invoicing.outstanding.not_invoiceable") : "",
          ]
            .filter(Boolean)
            .join(" · "),
          quantity: "1",
          unitPrice: String(Number(period.amount)),
          amount: Number(period.amount),
          blocked: period.already_billed ? t("invoicing.line.subscription_billed") : "",
        });
      }
    }
    return out;
  }

  const offers = $derived.by((): Offer[] => {
    if (kind === "hours") return hourOffers;
    if (kind === "subscription") {
      return [...periodOffers(subscriptions, "s"), ...periodOffers(domains, "d")];
    }
    return [];
  });

  const selectable = $derived(offers.filter((o) => !o.blocked));
  //: Anything capped or cycle-less that the list could not fully show. Reported, never
  //: silent: a truncated list that reads as "this is everything" is the worst answer here.
  const truncated = $derived(
    Boolean(hours?.truncated) ||
      subscriptions.some((s) => s.truncated) ||
      domains.some((d) => d.truncated),
  );

  let picked = $state<Record<string, boolean>>({});
  //: Reset each time the dialog opens: a tick left over from the previous client would add a
  //: line nobody chose. Keyed on `open` so re-opening is always a clean sheet.
  $effect(() => {
    if (open) picked = {};
  });

  const pickedIds = $derived(selectable.filter((o) => picked[o.id]).map((o) => o.id));
  const pickedTotal = $derived(
    selectable.filter((o) => picked[o.id]).reduce((sum, o) => sum + o.amount, 0),
  );
  const allPicked = $derived(selectable.length > 0 && pickedIds.length === selectable.length);

  function toggleAll() {
    const next = !allPicked;
    picked = Object.fromEntries(selectable.map((o) => [o.id, next]));
  }

  function confirm() {
    onadd(pickedIds);
    open = false;
  }

  const title = $derived(
    kind === "hours"
      ? t("invoicing.outstanding.hours_title")
      : t("invoicing.outstanding.subscriptions_title"),
  );
</script>

<Modal bind:open {title} size="3xl">
  <p class="mb-3 text-sm text-text-muted">
    {kind === "hours"
      ? t("invoicing.outstanding.hours_hint")
      : t("invoicing.outstanding.subscriptions_hint")}
  </p>

  {#if loading}
    <!-- "Loading", never "nothing outstanding": the two look identical and only one is true. -->
    <p class="py-6 text-center text-sm text-text-muted">{t("common.loading")}</p>
  {:else if offers.length === 0}
    <p class="py-6 text-center text-sm text-text-muted">
      {kind === "hours"
        ? t("invoicing.outstanding.hours_empty")
        : t("invoicing.outstanding.subscriptions_empty")}
    </p>
  {:else}
    {#if truncated}
      <p class="mb-3 rounded-lg border border-border px-3 py-2 text-xs text-text-muted">
        {t("invoicing.outstanding.truncated")}
      </p>
    {/if}
    <div class="max-h-96 overflow-y-auto rounded-lg border border-border">
      <table class="w-full text-sm">
        <thead class="sticky top-0 bg-surface-raised text-xs uppercase tracking-wide text-text-muted">
          <tr class="border-b border-border">
            <th class="w-8 px-3 py-2">
              <input
                type="checkbox"
                class="rounded border-border"
                checked={allPicked}
                onchange={toggleAll}
                disabled={selectable.length === 0}
                aria-label={t("common.select_all")}
              />
            </th>
            <th class="px-3 py-2 text-left font-semibold">{t("invoicing.line.description")}</th>
            <th class="px-3 py-2 text-right font-semibold">{t("invoicing.line.quantity")}</th>
            <th class="px-3 py-2 text-right font-semibold">{t("invoicing.line.amount")}</th>
          </tr>
        </thead>
        <tbody>
          {#each offers as offer (offer.id)}
            <tr class="border-b border-border last:border-0 {offer.blocked ? 'opacity-60' : ''}">
              <td class="px-3 py-2 align-top">
                <input
                  type="checkbox"
                  class="rounded border-border"
                  bind:checked={picked[offer.id]}
                  disabled={Boolean(offer.blocked)}
                  aria-label={offer.label}
                />
              </td>
              <td class="px-3 py-2">
                <span class="block text-text">{offer.label}</span>
                {#if offer.hint || offer.blocked}
                  <span class="block text-xs text-text-muted">
                    {[offer.hint, offer.blocked].filter(Boolean).join(" · ")}
                  </span>
                {/if}
              </td>
              <td class="px-3 py-2 text-right tabular-nums text-text-muted">{offer.quantity}</td>
              <td class="px-3 py-2 text-right tabular-nums text-text">{money(offer.amount)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <div class="mt-4 flex items-center justify-between gap-2">
    <span class="text-sm text-text-muted">
      {pickedIds.length > 0
        ? t("invoicing.outstanding.selected", {
            count: pickedIds.length,
            amount: money(pickedTotal),
          })
        : ""}
    </span>
    <div class="flex gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (open = false)}>{t("common.cancel")}</button
      >
      <Button disabled={pickedIds.length === 0} onclick={confirm}>
        {t("invoicing.outstanding.add")}
      </Button>
    </div>
  </div>
</Modal>

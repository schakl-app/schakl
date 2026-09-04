<script lang="ts">
  /**
   * An agreement, read-only (see `+page.server.ts`): what it is, what it costs, when it renews,
   * and what is in it. A client's page first — the staff edit lives in the list's modal, and
   * the one control staff get here is the way there.
   */
  import { page } from "$app/state";
  import { dateLocale, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Card from "$lib/core/ui/Card.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import { pageTitle } from "$lib/core/title";
  import { subscriptionTypeLabel } from "$lib/modules/subscriptions/types";

  let { data } = $props();

  const sub = $derived(data.subscription);
  const typeLabel = $derived(
    subscriptionTypeLabel(
      data.types.find((type) => type.id === sub.subscription_type_id),
      data.locale,
    ),
  );
  const money = (value: string | number | null | undefined) =>
    value == null
      ? "—"
      : new Intl.NumberFormat(dateLocale(), {
          style: "currency",
          currency: sub.currency || "EUR",
          trailingZeroDisplay: "stripIfInteger",
        }).format(Number(value));
  const interval = $derived(
    sub.interval_count > 1
      ? `${t(`subscriptions.interval.${sub.interval}`)} · ${t("subscriptions.detail.interval_count", { count: sub.interval_count })}`
      : t(`subscriptions.interval.${sub.interval}`),
  );
  const lines = $derived(sub.lines ?? []);
  const usage = $derived(sub.usage ?? null);
</script>

<svelte:head>
  <title>{pageTitle(sub.name)}</title>
</svelte:head>

<PageHeader title={sub.name}>
  {#snippet subtitle()}
    {#if sub.company_name}
      <a href={`/companies/${sub.company_id}`} class="hover:underline">{sub.company_name}</a>
      {#if typeLabel}
        · {typeLabel}
      {/if}
    {:else if typeLabel}
      {typeLabel}
    {/if}
  {/snippet}
  {#snippet actions()}
    <span class="rounded-md bg-surface px-2 py-1 text-xs text-text-muted"
      >{t(`subscriptions.status.${sub.status}`)}</span
    >
    {#if data.canWrite}
      <a
        href={`/subscriptions?company=${sub.company_id}`}
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface"
      >
        {t("subscriptions.detail.manage")}
      </a>
    {/if}
  {/snippet}
</PageHeader>

<div class="grid gap-6 lg:grid-cols-2">
  <Card title={t("subscriptions.detail.title")}>
    <dl class="space-y-2 text-sm">
      <div class="flex justify-between gap-3">
        <dt class="text-text-muted">{t("subscriptions.field.amount")}</dt>
        <dd class="text-text tabular-nums">{money(sub.amount)} · {interval}</dd>
      </div>
      <div class="flex justify-between gap-3">
        <dt class="text-text-muted">{t("subscriptions.field.start_date")}</dt>
        <dd class="text-text">{fmtNumericDate(sub.start_date)}</dd>
      </div>
      {#if sub.end_date}
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("subscriptions.field.end_date")}</dt>
          <dd class="text-text">{fmtNumericDate(sub.end_date)}</dd>
        </div>
      {/if}
      <div class="flex justify-between gap-3">
        <dt class="text-text-muted">{t("subscriptions.field.next_invoice")}</dt>
        <dd class="text-text">
          {sub.next_invoice_date ? fmtNumericDate(sub.next_invoice_date) : "—"}
        </dd>
      </div>
      {#if sub.included_hours != null}
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("subscriptions.field.included_hours")}</dt>
          <dd class="text-text tabular-nums">
            {#if usage}
              {t("subscriptions.detail.hours_used", {
                used: usage.used_hours.toLocaleString(data.locale, { maximumFractionDigits: 1 }),
                included: Number(sub.included_hours).toLocaleString(data.locale, {
                  maximumFractionDigits: 1,
                }),
              })}
            {:else}
              {Number(sub.included_hours).toLocaleString(data.locale, { maximumFractionDigits: 1 })}
            {/if}
          </dd>
        </div>
      {/if}
      {#if sub.notice_period_days != null}
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("subscriptions.field.notice_period_days")}</dt>
          <dd class="text-text tabular-nums">{sub.notice_period_days}</dd>
        </div>
      {/if}
    </dl>
  </Card>

  <Card title={t("subscriptions.detail.lines")}>
    {#if lines.length === 0}
      <p class="text-sm text-text-muted">{t("subscriptions.detail.no_lines")}</p>
    {:else}
      <ul class="divide-y divide-border text-sm">
        {#each lines as line (line.id)}
          <li class="flex items-center justify-between gap-3 py-2">
            <span class="min-w-0 flex-1 text-text">{line.description}</span>
            <span class="shrink-0 tabular-nums text-text-muted">
              {Number(line.quantity).toLocaleString(data.locale)} × {money(line.unit_amount)}
            </span>
          </li>
        {/each}
      </ul>
    {/if}
  </Card>

  {#if sub.notes && !page.data.user?.isPortal}
    <Card title={t("subscriptions.field.notes")}>
      <p class="whitespace-pre-line text-sm text-text">{sub.notes}</p>
    </Card>
  {/if}
</div>

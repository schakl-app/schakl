<script lang="ts">
  /**
   * An agreement, read-only (see `+page.server.ts`): what it is, what it costs, when it renews,
   * and what is in it. A client's page first — the staff edit lives in the list's modal, and
   * the one control staff get here is the way there.
   */
  import { page } from "$app/state";
  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";
  import { applicableDefinitions } from "$lib/core/customfields/scope";
  import { dateLocale, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { entityPanelComponent } from "$lib/core/registry";
  import Card from "$lib/core/ui/Card.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import { pageTitle } from "$lib/core/title";
  import { subscriptionTypeLabel } from "$lib/modules/subscriptions/types";
  import {
    resolveNoteVariables,
    subscriptionNoteValues,
  } from "$lib/modules/subscriptions/variables";

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
  // The notes resolve their variables for reading (#259) and render as the markdown they
  // are — the same text, marked up the same way, that the invoice prints when the flag is on.
  const notesDisplay = $derived(
    resolveNoteVariables(
      sub.notes ?? "",
      subscriptionNoteValues({
        companyName: sub.company_name,
        subscriptionName: sub.name,
        typeLabel: sub.subscription_type_id ? typeLabel : null,
        amount: sub.amount,
        interval: sub.interval,
        includedHours: sub.included_hours,
        startDate: sub.start_date,
        brandName: page.data.theme?.brandName ?? null,
      }),
    ),
  );
  // What the agreement covers — the sites it keeps online first, then the work it pays for.
  const LINK_ORDER = { website: 0, project: 1, task: 2 } as const;
  const LINK_HREF = { website: "/websites", project: "/projects", task: "/tasks" } as const;
  const links = $derived(
    (sub.links ?? []).toSorted((a, b) => LINK_ORDER[a.entity_type] - LINK_ORDER[b.entity_type]),
  );

  // The tenant's own fields that apply to *this* agreement — a field attached to another type
  // is not drawn here even where the row still holds a value for it (§13) — and hold a value.
  const customValues = $derived((sub.custom ?? {}) as Record<string, unknown>);
  const customDefinitions = $derived(
    applicableDefinitions(data.definitions, {
      subscription_type_id: sub.subscription_type_id ?? null,
      subscription_template_id: sub.subscription_template_id ?? null,
    }).filter((def) => {
      const value = customValues[def.key];
      return (
        value !== undefined &&
        value !== null &&
        value !== "" &&
        !(Array.isArray(value) && value.length === 0)
      );
    }),
  );

  // Typed entity panels (the invoiced periods, contributed by `invoicing`) — composed, never
  // imported, the way the domain page does it.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  function panelComponent(key: string) {
    return entityPanelComponent(enabled, "subscription", key);
  }
  const emptyLookups = { members: [], companies: [], projects: [], tasks: [] };
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
      {#if sub.billed_until}
        <!-- The operator's "already invoiced up to" statement: drawn only when made. -->
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("subscriptions.field.billed_until")}</dt>
          <dd class="text-text">{fmtNumericDate(sub.billed_until)}</dd>
        </div>
      {/if}
      <!-- Resolved through the agreement's own say, the preset and the type: which period
           the next invoice covers — and whether this agreement decided for itself. -->
      <div class="flex justify-between gap-3">
        <dt class="text-text-muted">{t("subscriptions.field.billing_direction")}</dt>
        <dd class="text-text">
          {sub.billed_in_advance
            ? t("subscriptions.billing_direction.advance_short")
            : t("subscriptions.billing_direction.arrears_short")}
          {#if sub.billed_in_advance_override != null}
            <span class="text-xs text-text-muted"
              >({t("subscriptions.billing_direction.own_setting")})</span
            >
          {/if}
        </dd>
      </div>
      {#if sub.notes && !page.data.user?.isPortal}
        <!-- Whether the notes below reach the client's invoice — resolved through the
             agreement's own say and the preset, and whether this agreement decided itself. -->
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("subscriptions.field.notes_on_invoice")}</dt>
          <dd class="text-text">
            {sub.notes_on_invoice
              ? t("subscriptions.notes_on_invoice.on_short")
              : t("subscriptions.notes_on_invoice.off_short")}
            {#if sub.notes_on_invoice_override != null}
              <span class="text-xs text-text-muted"
                >({t("subscriptions.notes_on_invoice.own_setting")})</span
              >
            {/if}
          </dd>
        </div>
      {/if}
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

  {#if customDefinitions.length > 0}
    <Card title={t("subscriptions.detail.custom_fields")}>
      <CustomFieldsView
        definitions={customDefinitions}
        values={customValues}
        locale={data.locale}
      />
    </Card>
  {/if}

  {#if links.length > 0}
    <Card title={t("subscriptions.detail.covers")}>
      <ul class="divide-y divide-border text-sm">
        {#each links as link (link.id)}
          <li class="flex items-center justify-between gap-3 py-2">
            <a
              href={`${LINK_HREF[link.entity_type]}/${link.entity_id}`}
              class="min-w-0 flex-1 truncate text-brand hover:underline">{link.label ?? "—"}</a
            >
            <span class="shrink-0 rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
              >{t(`subscriptions.link_kind.${link.entity_type}`)}</span
            >
          </li>
        {/each}
      </ul>
    </Card>
  {/if}

  {#if sub.notes && !page.data.user?.isPortal}
    <Card title={t("subscriptions.field.notes")}>
      <Markdown value={notesDisplay} class="text-sm text-text" />
    </Card>
  {/if}

  {#each data.panels as panel (panel.key)}
    {@const PanelComponent = panelComponent(panel.key)}
    {#if PanelComponent}
      <Card title={t(panel.titleKey)} kind={panel.prominence === "register" ? "register" : "panel"}>
        <PanelComponent data={panel.data} context={data.context} lookups={emptyLookups} />
      </Card>
    {/if}
  {/each}
</div>

<script lang="ts">
  /**
   * One ad account: its campaigns, what each cost and returned, and the three things a person
   * does to one from here — pause it, switch it on, change what it may spend.
   *
   * **Building a campaign is not on this screen.** A campaign is a dozen decisions in a
   * vocabulary Meta changes twice a year, and Ads Manager is the form for it; what is built
   * *from here* is built through the API, by an agent holding a key (docs/META.md). What this
   * screen is for is the answer to "how is it doing" and the three acts that answer leads to.
   *
   * **Switching on confirms; pausing does not.** One of them can spend a client's money.
   */
  import Pause from "@lucide/svelte/icons/pause";
  import Play from "@lucide/svelte/icons/play";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import Wallet from "@lucide/svelte/icons/wallet";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu, { type ActionItem } from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Card from "$lib/core/ui/Card.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";
  import {
    fmtAmount,
    fmtCents,
    fmtCount,
    fmtPercent,
    objectiveLabel,
    parseAmount,
    PERIODS,
    statusLabel,
    statusState,
  } from "$lib/integrations/meta_ads/format";

  let { data, form } = $props();

  type Settled<T> = Awaited<T>;
  type Campaigns = Settled<typeof data.campaigns>;
  type Insights = Settled<typeof data.insights>;
  type AdSets = Settled<NonNullable<typeof data.adsets>>;
  type Ads = Settled<NonNullable<typeof data.ads>>;

  const busy = new InFlight();
  const currency = $derived(data.account.currency);

  // --- streamed reads, settled into state ------------------------------------------------------
  // Never a raw {#await}: a refresh would blank the table. The guard drops a resolution that
  // belongs to a load the page has already left (docs/PERFORMANCE.md).
  let campaigns = $state<Campaigns | null>(null);
  let insights = $state<Insights | null>(null);
  let adsets = $state<AdSets | null>(null);
  let ads = $state<Ads | null>(null);
  let loading = $state(true);

  $effect(() => {
    const promise = data.campaigns;
    loading = true;
    void promise.then((value) => {
      if (data.campaigns !== promise) return;
      campaigns = value;
      loading = false;
    });
  });
  $effect(() => {
    const promise = data.insights;
    void promise.then((value) => {
      if (data.insights === promise) insights = value;
    });
  });
  $effect(() => {
    const promise = data.adsets;
    if (!promise) {
      adsets = null;
      return;
    }
    void promise.then((value) => {
      if (data.adsets === promise) adsets = value;
    });
  });
  $effect(() => {
    const promise = data.ads;
    if (!promise) {
      ads = null;
      return;
    }
    void promise.then((value) => {
      if (data.ads === promise) ads = value;
    });
  });

  const rows = $derived(campaigns?.data ?? []);
  const figures = $derived(
    new Map((insights?.data?.rows ?? []).map((row) => [row.meta_id ?? "", row])),
  );
  const totals = $derived(insights?.data?.totals ?? null);
  const opened = $derived(rows.find((row) => row.meta_id === data.campaign) ?? null);

  function href(params: Record<string, string>): string {
    const next = new URL(page.url);
    for (const [key, value] of Object.entries(params)) {
      if (value) next.searchParams.set(key, value);
      else next.searchParams.delete(key);
    }
    return `${next.pathname}${next.search}`;
  }

  // --- acts ----------------------------------------------------------------------------------
  interface Subject {
    kind: "campaigns" | "adsets" | "ads";
    meta_id: string;
    name: string;
    daily: number | null;
    lifetime: number | null;
  }

  let activating = $state<Subject | null>(null);
  let activateOpen = $state(false);
  let budgeting = $state<Subject | null>(null);
  let budgetOpen = $state(false);
  let amount = $state("");
  let reason = $state("");
  let pauseForm = $state<HTMLFormElement | null>(null);
  let pausing = $state<Subject | null>(null);
  let dialogError = $state<string | null>(null);

  const which = $derived(budgeting?.lifetime && !budgeting.daily ? "lifetime" : "daily");
  const cents = $derived(parseAmount(amount));

  function menu(subject: Subject, status: string | null | undefined): ActionItem[] {
    const items: ActionItem[] = [];
    if (status !== "ACTIVE" && data.canActivate) {
      items.push({
        label: t("meta_ads.action.activate"),
        icon: Play,
        onclick: () => {
          activating = subject;
          dialogError = null;
          activateOpen = true;
        },
      });
    }
    if (status === "ACTIVE" && data.canWrite) {
      items.push({
        label: t("meta_ads.action.pause"),
        icon: Pause,
        onclick: async () => {
          pausing = subject;
          await Promise.resolve();
          pauseForm?.requestSubmit();
        },
      });
    }
    if (subject.kind !== "ads" && data.canBudget && (subject.daily || subject.lifetime)) {
      items.push({
        label: t("meta_ads.action.budget"),
        icon: Wallet,
        onclick: () => {
          budgeting = subject;
          amount = "";
          reason = "";
          budgetOpen = true;
        },
      });
    }
    return items;
  }

  /** A refusal as a sentence, with the numbers it carries printed as money. */
  function refused(result: typeof form): string | null {
    if (!result || !("error" in result) || !result.error) return null;
    const details = ("details" in result ? result.details : null) as Record<string, unknown> | null;
    const money = (value: unknown) =>
      typeof value === "number" ? fmtCents(value, currency) : String(value ?? "");
    return t(result.error, {
      limit: money(details?.limit),
      value: money(details?.value),
      previous: money(details?.previous),
      per: String(details?.per ?? ""),
    });
  }
</script>

<svelte:head>
  <title>{pageTitle(data.account.name)}</title>
</svelte:head>

{#if !data.account.can_write}
  <p class="mb-4 rounded-lg bg-surface-tint px-4 py-3 text-sm text-text">
    {t("meta_ads.page.read_only")}
  </p>
{/if}

{#if form && "done" in form && form.done}
  <p class="mb-4 rounded-lg bg-surface-tint px-4 py-3 text-sm text-text" role="status">
    {t(`meta_ads.outcome.${form.done}`)}
  </p>
{:else if refused(form) && !budgetOpen}
  <p
    class="mb-4 flex items-start gap-2 rounded-lg border border-border bg-surface-raised px-4 py-3 text-sm text-text"
    role="alert"
  >
    <TriangleAlert
      size={16}
      class="mt-0.5 shrink-0 text-red-600 dark:text-red-400"
      aria-hidden="true"
    />
    {refused(form)}
  </p>
{/if}

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <nav class="flex flex-wrap gap-1" aria-label={t("meta_ads.period.label")}>
    {#each PERIODS as period (period)}
      <a
        href={href({ period: period === "30d" ? "" : period })}
        class="rounded-full px-3 py-1 text-sm {data.period === period
          ? 'bg-surface-tint font-medium text-text ring-1 ring-border'
          : 'text-text-muted hover:text-text'}"
        aria-current={data.period === period ? "page" : undefined}
      >
        {t(`meta_ads.period.${period}`)}
      </a>
    {/each}
  </nav>
  {#if insights?.data}
    <!-- A figure is a claim about a span, so the span is on the screen (#312). -->
    <p class="text-xs tabular-nums text-text-muted">
      {fmtPeriod(insights.data.date_from, insights.data.date_to)}
    </p>
  {/if}
</div>

<div class="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
  {#each [{ label: t("meta_ads.metric.spend"), value: totals ? fmtAmount(totals.spend, currency) : null }, { label: t("meta_ads.metric.impressions"), value: totals ? fmtCount(totals.impressions) : null }, { label: t("meta_ads.metric.clicks"), value: totals ? fmtCount(totals.clicks) : null }, { label: t("meta_ads.metric.ctr"), value: totals ? fmtPercent(totals.ctr) : null }] as tile (tile.label)}
    <Card kind="stat">
      <p class="text-xs text-text-muted">{tile.label}</p>
      <p class="mt-1 text-xl font-semibold tabular-nums text-text">
        {#if tile.value !== null}{tile.value}
        {:else if insights === null}<span class="text-sm font-normal text-text-muted"
            >{t("meta_ads.loading")}</span
          >
        {:else}—{/if}
      </p>
    </Card>
  {/each}
</div>

{#each insights?.data?.warnings ?? [] as warning (warning)}
  <p class="mb-3 text-xs text-text-muted">{t(warning)}</p>
{/each}
{#if insights?.errorKey}
  <p class="mb-3 text-sm text-text" role="alert">
    {t("meta_ads.page.insights_failed")}
    {t(insights.errorKey)}
  </p>
{/if}

<Card title={t("meta_ads.view.campaigns")}>
  {#if loading && campaigns === null}
    <p class="py-6 text-center text-sm text-text-muted">{t("meta_ads.loading")}</p>
  {:else if campaigns?.errorKey}
    <p class="flex items-start gap-2 py-4 text-sm text-text" role="alert">
      <TriangleAlert size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
      {t(campaigns.errorKey)}
    </p>
  {:else if rows.length === 0}
    <p class="py-6 text-center text-sm text-text-muted">{t("meta_ads.page.no_campaigns")}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="w-full min-w-[46rem] text-sm">
        <thead>
          <tr class="border-b border-border text-left text-xs text-text-muted">
            <th scope="col" class="py-2 pr-3 font-medium">{t("meta_ads.column.campaign")}</th>
            <th scope="col" class="px-3 py-2 font-medium">{t("meta.list.status")}</th>
            <th scope="col" class="px-3 py-2 text-right font-medium"
              >{t("meta_ads.column.budget")}</th
            >
            <th scope="col" class="px-3 py-2 text-right font-medium"
              >{t("meta_ads.metric.spend")}</th
            >
            <th scope="col" class="px-3 py-2 text-right font-medium"
              >{t("meta_ads.metric.impressions")}</th
            >
            <th scope="col" class="px-3 py-2 text-right font-medium"
              >{t("meta_ads.metric.clicks")}</th
            >
            <th scope="col" class="px-3 py-2 text-right font-medium">{t("meta_ads.metric.ctr")}</th>
            <th scope="col" class="w-10 py-2"><span class="sr-only">{t("common.actions")}</span></th
            >
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each rows as row (row.meta_id)}
            {@const figure = figures.get(row.meta_id)}
            {@const subject = {
              kind: "campaigns" as const,
              meta_id: row.meta_id,
              name: row.name,
              daily: row.daily_budget_cents ?? null,
              lifetime: row.lifetime_budget_cents ?? null,
            }}
            {@const items = menu(subject, row.status)}
            <tr class={data.campaign === row.meta_id ? "bg-surface-tint" : ""}>
              <td class="max-w-0 py-2.5 pr-3">
                <a
                  href={href({ campaign: data.campaign === row.meta_id ? "" : row.meta_id })}
                  class="block truncate font-medium text-text hover:text-brand"
                  data-sveltekit-noscroll
                >
                  {row.name}
                </a>
                <span class="block truncate text-xs text-text-muted">
                  {objectiveLabel(row.objective)}
                </span>
              </td>
              <td class="px-3 py-2.5">
                {#if statusState(row.effective_status) === "neutral"}
                  <span class="text-xs text-text-muted">{statusLabel(row.effective_status)}</span>
                {:else}
                  <StateMark
                    state={statusState(row.effective_status)}
                    label={statusLabel(row.effective_status)}
                    variant="chip"
                  />
                {/if}
              </td>
              <td class="whitespace-nowrap px-3 py-2.5 text-right tabular-nums text-text">
                {#if row.daily_budget_cents}
                  {fmtCents(row.daily_budget_cents, currency)}
                  <span class="text-xs text-text-muted">{t("meta_ads.per_day")}</span>
                {:else if row.lifetime_budget_cents}
                  {fmtCents(row.lifetime_budget_cents, currency)}
                  <span class="text-xs text-text-muted">{t("meta_ads.lifetime")}</span>
                {:else}
                  <span class="text-xs text-text-muted">{t("meta_ads.budget_on_adsets")}</span>
                {/if}
              </td>
              <td class="px-3 py-2.5 text-right tabular-nums text-text">
                {figure ? fmtAmount(figure.spend, currency) : "—"}
              </td>
              <td class="px-3 py-2.5 text-right tabular-nums text-text-muted">
                {figure ? fmtCount(figure.impressions) : "—"}
              </td>
              <td class="px-3 py-2.5 text-right tabular-nums text-text-muted">
                {figure ? fmtCount(figure.clicks) : "—"}
              </td>
              <td class="px-3 py-2.5 text-right tabular-nums text-text-muted">
                {figure ? fmtPercent(figure.ctr) : "—"}
              </td>
              <td class="py-2.5 text-right">
                {#if items.length > 0}<ActionsMenu {items} compact />{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if !data.campaign}
      <p class="mt-3 text-xs text-text-muted">{t("meta_ads.page.open_hint")}</p>
    {/if}
  {/if}
</Card>

{#if data.campaign}
  <!-- Which campaign the two cards below are about, said above them and not after. -->
  <div class="mt-6 flex flex-wrap items-baseline justify-between gap-2">
    <h2 class="text-sm font-semibold text-text">
      {opened ? t("meta_ads.page.opened", { name: opened.name }) : t("meta_ads.view.adsets")}
    </h2>
    <a
      href={href({ campaign: "" })}
      class="text-sm text-brand hover:underline"
      data-sveltekit-noscroll>{t("common.close")}</a
    >
  </div>
  <div class="mt-3 grid gap-6 lg:grid-cols-2">
    <Card title={t("meta_ads.view.adsets")}>
      {#if adsets === null}
        <p class="py-4 text-sm text-text-muted">{t("meta_ads.loading")}</p>
      {:else if adsets.errorKey}
        <p class="py-4 text-sm text-text" role="alert">{t(adsets.errorKey)}</p>
      {:else if (adsets.data ?? []).length === 0}
        <p class="py-4 text-sm text-text-muted">{t("meta_ads.page.no_adsets")}</p>
      {:else}
        <ul class="divide-y divide-border">
          {#each adsets.data ?? [] as row (row.meta_id)}
            {@const subject = {
              kind: "adsets" as const,
              meta_id: row.meta_id,
              name: row.name,
              daily: row.daily_budget_cents ?? null,
              lifetime: row.lifetime_budget_cents ?? null,
            }}
            {@const items = menu(subject, row.status)}
            {@const countries = (
              (row.targeting?.geo_locations as { countries?: string[] } | undefined)?.countries ??
              []
            ).join(", ")}
            <li class="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-text">{row.name}</p>
                <p class="mt-0.5 truncate text-xs text-text-muted">
                  {[
                    statusLabel(row.effective_status),
                    row.daily_budget_cents
                      ? `${fmtCents(row.daily_budget_cents, currency)} ${t("meta_ads.per_day")}`
                      : row.lifetime_budget_cents
                        ? `${fmtCents(row.lifetime_budget_cents, currency)} ${t("meta_ads.lifetime")}`
                        : "",
                    countries,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {#if row.dsa_beneficiary || row.dsa_payor}
                  <p class="mt-0.5 truncate text-xs text-text-muted">
                    {t("meta_ads.dsa", {
                      beneficiary: row.dsa_beneficiary ?? "—",
                      payor: row.dsa_payor ?? "—",
                    })}
                  </p>
                {/if}
              </div>
              {#if items.length > 0}<ActionsMenu {items} compact />{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </Card>

    <Card title={t("meta_ads.view.ads")}>
      {#if ads === null}
        <p class="py-4 text-sm text-text-muted">{t("meta_ads.loading")}</p>
      {:else if ads.errorKey}
        <p class="py-4 text-sm text-text" role="alert">{t(ads.errorKey)}</p>
      {:else if (ads.data ?? []).length === 0}
        <p class="py-4 text-sm text-text-muted">{t("meta_ads.page.no_ads")}</p>
      {:else}
        <ul class="divide-y divide-border">
          {#each ads.data ?? [] as row (row.meta_id)}
            {@const subject = {
              kind: "ads" as const,
              meta_id: row.meta_id,
              name: row.name,
              daily: null,
              lifetime: null,
            }}
            {@const items = menu(subject, row.status)}
            <li class="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-text">{row.name}</p>
                <p class="mt-1">
                  {#if statusState(row.effective_status) === "neutral"}
                    <span class="text-xs text-text-muted">{statusLabel(row.effective_status)}</span>
                  {:else}
                    <StateMark
                      state={statusState(row.effective_status)}
                      label={statusLabel(row.effective_status)}
                      variant="chip"
                    />
                  {/if}
                </p>
                {#each row.issues as issue, index (index)}
                  <!-- Meta's own account of what is wrong, as it sent it: the one sentence that
                       says what to fix, in whatever language Meta chose. -->
                  <p class="mt-1 break-words text-xs text-text">
                    {String(issue.error_summary ?? issue.error_message ?? issue.level ?? "")}
                  </p>
                {/each}
              </div>
              {#if items.length > 0}<ActionsMenu {items} compact />{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </Card>
  </div>
{/if}

<!-- Pausing needs no dialog: it stops spend, and a second press undoes it. clear(): it starts
     something rather than editing a field. -->
<form
  bind:this={pauseForm}
  method="POST"
  action="?/status"
  use:enhance={busy.clear("pause")}
  class="hidden"
>
  <input type="hidden" name="kind" value={pausing?.kind ?? ""} />
  <input type="hidden" name="meta_id" value={pausing?.meta_id ?? ""} />
  <input type="hidden" name="to" value="PAUSED" />
</form>

<ConfirmDialog
  bind:open={activateOpen}
  title={t("meta_ads.confirm.activate_title", { name: activating?.name ?? "" })}
  message={t("meta_ads.confirm.activate_message")}
  consequences={[
    t("meta_ads.confirm.activate_spends"),
    activating?.kind === "ads"
      ? t("meta_ads.confirm.activate_review")
      : t("meta_ads.confirm.activate_children"),
  ]}
  action="?/status"
  fields={{
    kind: activating?.kind ?? "",
    meta_id: activating?.meta_id ?? "",
    to: "ACTIVE",
  }}
  confirmLabel={t("meta_ads.action.activate")}
  variant="primary"
  onfailure={(key) => (dialogError = key)}
/>
{#if dialogError}
  <p class="mt-4 text-sm text-red-600 dark:text-red-400" role="alert">{t(dialogError)}</p>
{/if}

<Modal bind:open={budgetOpen} title={t("meta_ads.budget.title", { name: budgeting?.name ?? "" })}>
  <form
    method="POST"
    action="?/budget"
    use:enhance={busy.wrap("budget", () => async ({ result, update }) => {
      await update({ reset: false });
      if (result.type === "success") budgetOpen = false;
    })}
    class="space-y-4"
  >
    <input type="hidden" name="kind" value={budgeting?.kind ?? ""} />
    <input type="hidden" name="meta_id" value={budgeting?.meta_id ?? ""} />
    <input type="hidden" name="which" value={which} />
    <input type="hidden" name="cents" value={cents ?? ""} />
    <p class="text-sm text-text-muted">
      {t(which === "daily" ? "meta_ads.budget.now_daily" : "meta_ads.budget.now_lifetime", {
        amount: fmtCents(
          which === "daily" ? (budgeting?.daily ?? null) : (budgeting?.lifetime ?? null),
          currency,
        ),
      })}
    </p>
    <div>
      <label for="meta-ads-amount" class="mb-1 block text-sm font-medium text-text">
        {t(which === "daily" ? "meta_ads.budget.new_daily" : "meta_ads.budget.new_lifetime", {
          currency: currency ?? "",
        })}
      </label>
      <input
        id="meta-ads-amount"
        inputmode="decimal"
        autocomplete="off"
        bind:value={amount}
        placeholder="0,00"
        class="w-40 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm tabular-nums text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      />
      {#if amount && cents === null}
        <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
          {t("errors.meta_ads_budget_invalid")}
        </p>
      {:else if cents !== null}
        <p class="mt-1 text-xs text-text-muted">{fmtCents(cents, currency)}</p>
      {/if}
    </div>
    <div>
      <label for="meta-ads-reason" class="mb-1 block text-sm font-medium text-text">
        {t("meta_ads.budget.reason")}
      </label>
      <input
        id="meta-ads-reason"
        name="reason"
        bind:value={reason}
        maxlength="2000"
        placeholder={t("meta_ads.budget.reason_placeholder")}
        class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      />
      <p class="mt-1 text-xs text-text-muted">{t("meta_ads.budget.reason_hint")}</p>
    </div>
    {#if refused(form)}
      <!-- The refusal, with the ceiling it hit, beside the field that hit it (#305). -->
      <p class="text-sm text-red-600 dark:text-red-400" role="alert">{refused(form)}</p>
    {/if}
    <div class="flex justify-end gap-2 border-t border-border pt-4">
      <Button type="button" variant="secondary" onclick={() => (budgetOpen = false)}>
        {t("common.cancel")}
      </Button>
      <Button type="submit" loading={busy.is("budget")} disabled={busy.active || cents === null}>
        {t("meta_ads.action.budget")}
      </Button>
    </div>
  </form>
</Modal>

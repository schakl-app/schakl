<script lang="ts">
  /**
   * The guardrails, in two layers that are drawn as two.
   *
   * **An empty field inherits; it never means "no limit".** So an account's empty box shows,
   * as its placeholder, the value it is actually running on — the house's, or the built-in —
   * and says in words where that value comes from. A limit is lifted by stating a larger one.
   *
   * The house policy is on this screen too, under the account's, because an agency sets a
   * ceiling while looking at the account that made it want one.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import Card from "$lib/core/ui/Card.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { fmtCents } from "$lib/integrations/meta_ads/format";

  let { data, form } = $props();

  const busy = new InFlight();
  const currency = $derived(data.account.currency);
  let clearing = $state(false);

  type Values = typeof data.policy.own;

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  /** Cents as the text somebody would type: "25" or "25,50". */
  function typed(cents: number | null | undefined): string {
    if (cents === null || cents === undefined) return "";
    const whole = cents / 100;
    return Number.isInteger(whole) ? String(whole) : whole.toFixed(2).replace(".", ",");
  }

  const refused = (layer: string, field: string) =>
    form && "fields" in form && form.layer === layer ? (form.fields?.[field] ?? null) : null;
  const failed = (layer: string) =>
    form && "error" in form && form.layer === layer && !form.fields ? form.error : null;
  const hasOwn = $derived(
    Object.entries(data.policy.own).some(([, value]) =>
      Array.isArray(value) ? value.length > 0 : value !== null && value !== "",
    ),
  );
</script>

<svelte:head>
  <title>{pageTitle(t("meta_ads.view.policy"))}</title>
</svelte:head>

{#snippet fields(layer: "own" | "house", own: Values, inherited: Values | null)}
  <div class="grid gap-4 sm:grid-cols-3">
    <div>
      <label for="{layer}-daily" class="mb-1 block text-sm font-medium text-text">
        {t("meta_ads.policy.max_daily", { currency: currency ?? "" })}
      </label>
      <input
        id="{layer}-daily"
        name="max_daily_budget"
        inputmode="decimal"
        autocomplete="off"
        value={typed(own.max_daily_budget_cents)}
        placeholder={inherited?.max_daily_budget_cents
          ? typed(inherited.max_daily_budget_cents)
          : t("meta_ads.policy.none")}
        class="{inputClass} tabular-nums"
      />
      {#if refused(layer, "max_daily_budget")}
        <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
          {t(refused(layer, "max_daily_budget")!)}
        </p>
      {/if}
    </div>
    <div>
      <label for="{layer}-lifetime" class="mb-1 block text-sm font-medium text-text">
        {t("meta_ads.policy.max_lifetime", { currency: currency ?? "" })}
      </label>
      <input
        id="{layer}-lifetime"
        name="max_lifetime_budget"
        inputmode="decimal"
        autocomplete="off"
        value={typed(own.max_lifetime_budget_cents)}
        placeholder={inherited?.max_lifetime_budget_cents
          ? typed(inherited.max_lifetime_budget_cents)
          : t("meta_ads.policy.none")}
        class="{inputClass} tabular-nums"
      />
      {#if refused(layer, "max_lifetime_budget")}
        <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
          {t(refused(layer, "max_lifetime_budget")!)}
        </p>
      {/if}
    </div>
    <div>
      <label for="{layer}-increase" class="mb-1 block text-sm font-medium text-text">
        {t("meta_ads.policy.max_increase")}
      </label>
      <input
        id="{layer}-increase"
        name="max_budget_increase_pct"
        inputmode="numeric"
        autocomplete="off"
        value={own.max_budget_increase_pct ?? ""}
        placeholder={String(inherited?.max_budget_increase_pct ?? 100)}
        class="{inputClass} tabular-nums"
      />
      {#if refused(layer, "max_budget_increase_pct")}
        <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
          {t(refused(layer, "max_budget_increase_pct")!)}
        </p>
      {/if}
    </div>
  </div>
  <p class="mt-1.5 text-xs text-text-muted">{t("meta_ads.policy.limits_hint")}</p>

  <div class="mt-4">
    <label for="{layer}-phrases" class="mb-1 block text-sm font-medium text-text">
      {t("meta_ads.policy.banned")}
    </label>
    <textarea
      id="{layer}-phrases"
      name="banned_phrases"
      rows="3"
      class="{inputClass} resize-y"
      value={(own.banned_phrases ?? []).join("\n")}></textarea>
    <p class="mt-1 text-xs text-text-muted">
      {t("meta_ads.policy.banned_hint")}
      {#if layer === "own" && (data.house.own.banned_phrases ?? []).length > 0}
        {t("meta_ads.policy.banned_inherited", {
          phrases: (data.house.own.banned_phrases ?? []).join(", "),
        })}
      {/if}
    </p>
  </div>

  <div class="mt-4 grid gap-4 sm:grid-cols-2">
    <div>
      <label for="{layer}-beneficiary" class="mb-1 block text-sm font-medium text-text">
        {t("meta_ads.policy.dsa_beneficiary")}
      </label>
      <input
        id="{layer}-beneficiary"
        name="dsa_beneficiary"
        maxlength="512"
        value={own.dsa_beneficiary ?? ""}
        placeholder={layer === "house"
          ? ""
          : (inherited?.dsa_beneficiary ?? data.account.company_name ?? "")}
        class={inputClass}
      />
    </div>
    <div>
      <label for="{layer}-payor" class="mb-1 block text-sm font-medium text-text">
        {t("meta_ads.policy.dsa_payor")}
      </label>
      <input
        id="{layer}-payor"
        name="dsa_payor"
        maxlength="512"
        value={own.dsa_payor ?? ""}
        placeholder={layer === "house"
          ? ""
          : (inherited?.dsa_payor ?? data.account.company_name ?? "")}
        class={inputClass}
      />
    </div>
  </div>
  <p class="mt-1.5 text-xs text-text-muted">
    {layer === "house" ? t("meta_ads.policy.dsa_hint_house") : t("meta_ads.policy.dsa_hint")}
  </p>

  <div class="mt-4">
    <label for="{layer}-steering" class="mb-1 block text-sm font-medium text-text">
      {t("meta_ads.policy.steering")}
    </label>
    <textarea
      id="{layer}-steering"
      name="steering"
      rows="3"
      maxlength="8000"
      class="{inputClass} resize-y"
      value={own.steering}></textarea>
    <p class="mt-1 text-xs text-text-muted">{t("meta_ads.policy.steering_hint")}</p>
  </div>
{/snippet}

<div class="max-w-3xl space-y-6">
  <Card title={t("meta_ads.policy.own_title", { name: data.account.name })}>
    <p class="mb-4 text-sm text-text-muted">
      {data.policy.effective.max_daily_budget_cents
        ? t("meta_ads.policy.effective", {
            daily: fmtCents(data.policy.effective.max_daily_budget_cents, currency),
            increase: String(data.policy.effective.max_budget_increase_pct ?? 100),
          })
        : t("meta_ads.policy.effective_open", {
            increase: String(data.policy.effective.max_budget_increase_pct ?? 100),
          })}
    </p>
    <!-- keep(): this edits something that exists. -->
    <form method="POST" action="?/save" use:enhance={busy.keep("own")}>
      {@render fields("own", data.policy.own, data.house.effective)}
      {#if failed("own")}
        <p class="mt-3 text-sm text-red-600 dark:text-red-400" role="alert">
          {t(failed("own")!)}
        </p>
      {/if}
      <div class="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <Button type="submit" loading={busy.is("own")} disabled={busy.active}>
          {t("common.save")}
        </Button>
        {#if hasOwn}
          <Button type="button" variant="secondary" onclick={() => (clearing = true)}>
            {t("meta_ads.policy.follow_house")}
          </Button>
        {/if}
        {#if form && "saved" in form && (form.saved === "own" || form.saved === "cleared")}
          <span class="text-sm text-text-muted" role="status">{t("common.saved")}</span>
        {/if}
      </div>
    </form>
  </Card>

  <Card kind="register" title={t("meta_ads.policy.house_title")}>
    <p class="mb-4 text-sm text-text-muted">{t("meta_ads.policy.house_hint")}</p>
    <form method="POST" action="?/saveHouse" use:enhance={busy.keep("house")}>
      {@render fields("house", data.house.own, null)}
      {#if failed("house")}
        <p class="mt-3 text-sm text-red-600 dark:text-red-400" role="alert">
          {t(failed("house")!)}
        </p>
      {/if}
      <div class="mt-5 flex items-center gap-3 border-t border-border pt-4">
        <Button type="submit" loading={busy.is("house")} disabled={busy.active}>
          {t("common.save")}
        </Button>
        {#if form && "saved" in form && form.saved === "house"}
          <span class="text-sm text-text-muted" role="status">{t("common.saved")}</span>
        {/if}
      </div>
    </form>
  </Card>
</div>

<ConfirmDialog
  bind:open={clearing}
  title={t("meta_ads.policy.follow_house")}
  message={t("meta_ads.policy.follow_house_message")}
  action="?/clear"
  confirmLabel={t("meta_ads.policy.follow_house")}
  variant="primary"
/>

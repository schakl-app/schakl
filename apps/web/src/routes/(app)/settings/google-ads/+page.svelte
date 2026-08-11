<script lang="ts">
  /**
   * Instellingen → Google Ads: the agency's developer token, the default manager account, the
   * write switch, and the linked advertisers.
   *
   * The token is write-only, mirroring every other credential screen here: the field loads empty
   * and a blank save keeps what is stored. Clearing it is a separate control, because "I did not
   * retype the secret" and "remove the secret" must never be the same gesture.
   */
  import { enhance } from "$app/forms";

  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";

  let { data, form } = $props();
  const settings = $derived(data.settings);

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  const companyName = $derived((id: string | null | undefined) =>
    id ? (data.companies.find((c) => c.id === id)?.name ?? "") : "",
  );
</script>

<svelte:head>
  <title>{pageTitle(t("settings.google_ads.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.google_ads.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.google_ads.subtitle")}</p>

{#if form?.error}
  <p class="mb-4 text-sm text-text">{t(form.error)}</p>
{/if}

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- keep(): this edits settings that already exist. The secret loads empty by design, but the
       manager id and the write switch carry saved values a reset would rewind. -->
  <form method="POST" action="?/save" use:enhance={busy.keep()} class="space-y-5">
    <div>
      <label for="gads-developer-token" class="mb-1 block text-sm font-medium text-text">
        {t("settings.google_ads.developer_token")}
      </label>
      <input
        id="gads-developer-token"
        name="developer_token"
        type="password"
        autocomplete="new-password"
        placeholder={settings?.developer_token_configured
          ? t("settings.google_ads.token_configured")
          : ""}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">
        {t("settings.google_ads.developer_token_hint")}
      </p>
      {#if settings?.env_token_configured && !settings?.developer_token_configured}
        <p class="mt-1 text-xs text-text-muted">{t("settings.google_ads.env_fallback_hint")}</p>
      {/if}
    </div>

    <div class="border-t border-border pt-5">
      <label for="gads-login-customer-id" class="mb-1 block text-sm font-medium text-text">
        {t("settings.google_ads.default_manager")}
      </label>
      <input
        id="gads-login-customer-id"
        name="default_login_customer_id"
        value={settings?.default_login_customer_id ?? ""}
        placeholder="840-880-4299"
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.google_ads.default_manager_hint")}</p>
    </div>

    <div class="flex items-start gap-2 border-t border-border pt-5">
      <input
        id="gads-writes-enabled"
        name="writes_enabled"
        type="checkbox"
        value="true"
        checked={settings?.writes_enabled ?? true}
        class="mt-0.5"
      />
      <label for="gads-writes-enabled" class="text-sm text-text">
        {t("settings.google_ads.writes_enabled")}
        <span class="mt-0.5 block text-xs text-text-muted">
          {t("settings.google_ads.writes_enabled_hint")}
        </span>
      </label>
    </div>

    <div class="flex items-center gap-3 border-t border-border pt-5">
      <Button type="submit" disabled={busy.active}>{t("common.save")}</Button>
      {#if form?.saved}<span class="text-sm text-text-muted">{t("common.saved")}</span>{/if}
    </div>
  </form>

  {#if settings?.developer_token_configured}
    <!-- Its own form and its own verb: blanking the field above keeps the secret, so removing it
         needs a control that says so. clear(): nothing here is being edited. -->
    <form
      method="POST"
      action="?/clearToken"
      use:enhance={busy.clear()}
      class="mt-4 border-t border-border pt-4"
    >
      <Button type="submit" variant="secondary" size="sm" disabled={busy.active}>
        {t("settings.google_ads.clear_token")}
      </Button>
    </form>
  {/if}
</section>

<section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.google_ads.accounts")}</h2>
  <p class="mb-4 text-xs text-text-muted">{t("settings.google_ads.accounts_hint")}</p>

  {#if data.accounts.length === 0}
    <p class="text-sm text-text-muted">{t("settings.google_ads.no_accounts")}</p>
  {:else}
    <ul class="mb-5 divide-y divide-border">
      {#each data.accounts as account (account.id)}
        <li class="flex items-start gap-3 py-3">
          <div class="min-w-0 flex-1">
            <span class="block truncate text-sm font-medium text-text"
              >{account.descriptive_name}</span
            >
            <span class="mt-0.5 block truncate text-xs text-text-muted">
              {account.customer_id_formatted}
              {#if account.company_id}· {companyName(account.company_id)}{/if}
              {#if !account.active}· {t("settings.google_ads.inactive")}{/if}
            </span>
            {#if account.status === "error" && account.last_error}
              <span class="mt-1 block break-words text-xs text-text">{account.last_error}</span>
            {:else if account.last_verified_at}
              <span class="mt-1 block text-xs text-text-muted">
                {t("google_ads.panel.verified")}
                {fmtNumericDate(account.last_verified_at)}
              </span>
            {/if}
          </div>
          <div class="flex shrink-0 gap-2">
            <!-- clear(): both start something rather than edit a field the user typed into. -->
            <form method="POST" action="?/verify" use:enhance={busy.clear()}>
              <input type="hidden" name="account_id" value={account.id} />
              <Button type="submit" variant="secondary" size="xs" disabled={busy.active}>
                {t("settings.google_ads.verify")}
              </Button>
            </form>
            {#if account.active}
              <form method="POST" action="?/unlink" use:enhance={busy.clear()}>
                <input type="hidden" name="account_id" value={account.id} />
                <Button type="submit" variant="secondary" size="xs" disabled={busy.active}>
                  {t("settings.google_ads.unlink")}
                </Button>
              </form>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  <!-- clear(): a create form starts something new, so it resets for the next one. -->
  <form
    method="POST"
    action="?/link"
    use:enhance={busy.clear()}
    class="space-y-3 border-t border-border pt-4"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="gads-customer-id" class="mb-1 block text-sm font-medium text-text">
          {t("settings.google_ads.customer_id")}
        </label>
        <input
          id="gads-customer-id"
          name="customer_id"
          required
          placeholder="124-264-3293"
          class={inputClass}
        />
      </div>
      <div>
        <label for="gads-account-name" class="mb-1 block text-sm font-medium text-text">
          {t("settings.google_ads.account_name")}
        </label>
        <input id="gads-account-name" name="descriptive_name" class={inputClass} />
      </div>
      <div>
        <label for="gads-company" class="mb-1 block text-sm font-medium text-text">
          {t("settings.google_ads.client")}
        </label>
        <select id="gads-company" name="company_id" class={inputClass}>
          <option value="">{t("settings.google_ads.no_client")}</option>
          {#each data.companies as company (company.id)}
            <option value={company.id}>{company.name}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="gads-manager" class="mb-1 block text-sm font-medium text-text">
          {t("settings.google_ads.manager")}
        </label>
        <input id="gads-manager" name="login_customer_id" class={inputClass} />
        <p class="mt-1 text-xs text-text-muted">{t("settings.google_ads.manager_hint")}</p>
      </div>
    </div>
    <Button type="submit" disabled={busy.active}>{t("settings.google_ads.link")}</Button>
  </form>
</section>

{#if data.mayPolicy}
  <!--
    The agency's own standing rules. Every account inherits these: the term lists are **added to**
    each account's rather than replaced by them, and the ceilings apply wherever an account sets
    none of its own. Per-client rules live on that client's own account screen, because that is
    where somebody looking at a client's search terms already is.
  -->
  <section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-base font-semibold text-text">{t("settings.google_ads.policy.title")}</h2>
    <p class="mb-4 text-sm text-text-muted">{t("settings.google_ads.policy.subtitle")}</p>
    {#if form?.policySaved}
      <p class="mb-3 text-sm text-text">{t("common.saved")}</p>
    {/if}
    <!-- keep(): an edit of rules that already exist, so a reset would blank every box. -->
    <form method="POST" action="?/policy" use:enhance={busy.keep("policy")} class="space-y-5">
      <div>
        <label for="gads-house-exclude" class="mb-1 block text-sm font-medium text-text">
          {t("google_ads.policy.exclude.title")}
        </label>
        <p class="mb-1 text-xs text-text-muted">{t("settings.google_ads.policy.exclude_hint")}</p>
        <textarea
          id="gads-house-exclude"
          name="always_exclude"
          rows="5"
          value={(data.policy?.always_exclude ?? []).join("\n")}
          class="{inputClass} font-mono"></textarea>
      </div>
      <div>
        <label for="gads-house-protected" class="mb-1 block text-sm font-medium text-text">
          {t("google_ads.policy.protected.title")}
        </label>
        <p class="mb-1 text-xs text-text-muted">{t("settings.google_ads.policy.protected_hint")}</p>
        <textarea
          id="gads-house-protected"
          name="protected_terms"
          rows="3"
          value={(data.policy?.protected_terms ?? []).join("\n")}
          class="{inputClass} font-mono"></textarea>
      </div>
      <div>
        <label for="gads-house-banned" class="mb-1 block text-sm font-medium text-text">
          {t("google_ads.policy.field.banned_phrases")}
        </label>
        <textarea
          id="gads-house-banned"
          name="banned_phrases"
          rows="3"
          value={(data.policy?.banned_phrases ?? []).join("\n")}
          class="{inputClass} font-mono"></textarea>
      </div>
      <div class="grid gap-4 sm:grid-cols-2">
        {#each [["max_daily_budget", data.policy?.max_daily_budget], ["max_budget_increase_pct", data.policy?.max_budget_increase_pct], ["max_cpc", data.policy?.max_cpc], ["waste_min_cost", data.policy?.waste_min_cost], ["waste_min_clicks", data.policy?.waste_min_clicks]] as [name, value] (name)}
          <div>
            <label for="gads-house-{name}" class="mb-1 block text-sm font-medium text-text">
              {t(`google_ads.policy.field.${name}`)}
            </label>
            <input
              id="gads-house-{name}"
              name={String(name)}
              inputmode="decimal"
              value={value ?? ""}
              placeholder={t("google_ads.policy.no_limit")}
              class={inputClass}
            />
          </div>
        {/each}
      </div>
      <div>
        <label for="gads-house-steering" class="mb-1 block text-sm font-medium text-text">
          {t("google_ads.policy.steering.title")}
        </label>
        <p class="mb-1 text-xs text-text-muted">{t("settings.google_ads.policy.steering_hint")}</p>
        <textarea
          id="gads-house-steering"
          name="steering"
          rows="3"
          value={data.policy?.steering ?? ""}
          class={inputClass}></textarea>
      </div>
      <div>
        <label for="gads-house-copy" class="mb-1 block text-sm font-medium text-text">
          {t("google_ads.policy.copy.title")}
        </label>
        <textarea
          id="gads-house-copy"
          name="ad_copy_rules"
          rows="3"
          value={data.policy?.ad_copy_rules ?? ""}
          class={inputClass}></textarea>
      </div>
      <Button type="submit" disabled={busy.active}>{t("common.save")}</Button>
    </form>
  </section>
{/if}

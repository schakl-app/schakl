<script lang="ts">
  /**
   * Instellingen → Mollie (epic #269, issue #267).
   *
   * One row per Mollie key, not a single "the Mollie account" setting, for the reason
   * `cloudflare_accounts` and `oxxa_accounts` are rows — and more sharply here: an agency
   * integrating payments holds a live key *and* a test key at the same time, so a singleton
   * would have made the second one an overwrite. For a payment credential that means either
   * taking real money in a test or failing to take any in production.
   *
   * Three things this screen exists to say out loud, none of which the other credential screens
   * have to.
   *
   * **Which world the key acts in.** Mollie's keys say so themselves (`test_…` / `live_…`), so
   * the mode is derived and never entered — but it still has to be *shown*, prominently, because
   * an agency that leaves a test key in place believes it is collecting money it is not. Hence a
   * badge on every row and a warning banner on a test one.
   *
   * **The notification URL.** Mollie posts to it when a payment changes, and it has to be
   * reachable from the public internet: behind an access proxy (docs/DEPLOY.md) somebody has to
   * allow that path. An admin who cannot see the URL cannot allow it, and the failure it causes
   * — payments collected at Mollie and never booked on the invoice — is completely silent.
   *
   * **Which methods the profile offers.** Filled by `verify`, an observation and never a
   * setting: methods are switched on in Mollie's own dashboard, and a list here that pretended
   * otherwise would be a second source of truth. It is also the only place "iDEAL is not
   * enabled" is visible before a client meets it at checkout.
   */
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import type { components } from "$lib/core/api/schema";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  type AccountRead = components["schemas"]["MollieAccountRead"];

  let { data, form } = $props();

  const accounts = $derived((data.accounts ?? []) as AccountRead[]);
  const providers = $derived(data.providers ?? []);

  const busy = new InFlight();
  let adding = $state(false);
  let editing = $state<string | null>(null);
  let deleteTarget = $state<AccountRead | null>(null);
  let confirmDelete = $state(false);
  /** Which row's notification URL was just copied — several rows, so this is keyed, not a flag. */
  let copied = $state<string | null>(null);

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /**
   * Live reads as a fact worth being sure of, test as something to notice: an agency that leaves
   * a test key connected sees an ordinary-looking payment screen and collects nothing.
   */
  const modeBadge: Record<string, string> = {
    live: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-400",
    test: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-400",
  };

  const modeLabel = (mode: string) =>
    mode === "test" ? t("mollie.accounts.mode_test") : t("mollie.accounts.mode_live");

  async function copyWebhookUrl(account: AccountRead) {
    if (!account.webhook_url) return;
    await navigator.clipboard.writeText(account.webhook_url);
    copied = account.id;
    setTimeout(() => {
      if (copied === account.id) copied = null;
    }, 2000);
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.mollie.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.mollie.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.mollie.subtitle")}</p>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}
{#if form?.saved}
  <p class="mb-4 text-sm text-green-600">{t("mollie.accounts.saved")}</p>
{/if}
{#if form?.verify}
  <!-- Saved and verified are independent answers, and both are reported: a rejected credential
       is still a stored one, so "Mollie weigert de sleutel" must never read as "niets opgeslagen".
       Mollie's own words follow, untranslated — they name the actual problem. -->
  <p class="mb-4 text-sm {form.verify.ok ? 'text-green-600' : 'text-red-600'}">
    {form.verify.ok ? t("mollie.accounts.verified") : t("mollie.accounts.verify_failed")}
    {#if form.verify.error}<span class="text-text-muted"> {form.verify.error}</span>{/if}
  </p>
{/if}

<section class="max-w-4xl space-y-4">
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <div>
      <h2 class="text-base font-medium text-text">{t("mollie.accounts.title")}</h2>
      <p class="text-sm text-text-muted">{t("mollie.accounts.intro")}</p>
    </div>
    <Button type="button" variant="secondary" size="sm" onclick={() => (adding = !adding)}>
      {t("mollie.accounts.add")}
    </Button>
  </div>

  {#if adding}
    <form
      method="POST"
      action="?/create"
      use:enhance={busy.clear("create")}
      class="rounded-xl border border-border bg-surface-raised p-5"
    >
      <div class="grid gap-4 sm:grid-cols-2">
        <div class="min-w-0">
          <label class={labelClass} for="new-name">{t("mollie.accounts.name")}</label>
          <input id="new-name" name="name" required class={inputClass} />
          <p class="mt-1 text-xs text-text-muted">{t("mollie.accounts.name_help")}</p>
        </div>
        {#if data.mayReadProviders}
          <div class="min-w-0">
            <label class={labelClass} for="new-provider">{t("mollie.accounts.provider")}</label>
            <select id="new-provider" name="provider_id" class={inputClass}>
              <option value="">—</option>
              {#each providers as provider (provider.id)}
                <option value={provider.id}>{provider.name}</option>
              {/each}
            </select>
            <p class="mt-1 text-xs text-text-muted">{t("mollie.accounts.provider_help")}</p>
          </div>
        {/if}
        <div class="min-w-0 sm:col-span-2">
          <label class={labelClass} for="new-key">{t("mollie.accounts.api_key")}</label>
          <!-- Write-only, like the Cloudflare token and the Google client secret: the API stores
               it encrypted and never plays it back. `new-password` keeps a password manager from
               offering the admin's own login here. -->
          <input
            id="new-key"
            name="api_key"
            type="password"
            autocomplete="new-password"
            required
            placeholder={t("mollie.accounts.api_key_placeholder")}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("mollie.accounts.api_key_help")}</p>
        </div>
      </div>
      <div class="mt-4">
        <Button type="submit" loading={busy.is("create")} disabled={busy.active}>
          {t("common.save")}
        </Button>
      </div>
    </form>
  {/if}

  {#if accounts.length === 0 && !adding}
    <p class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted">
      {t("mollie.accounts.empty")}
    </p>
  {/if}

  {#each accounts as account (account.id)}
    <article class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 class="flex flex-wrap items-center gap-2 text-sm font-medium text-text">
            <span class="truncate">{account.name}</span>
            <!-- The mode is the first thing to read on this row, not a detail two lines down. -->
            <span
              class="rounded-full px-2 py-0.5 text-[11px] font-medium {modeBadge[account.mode] ??
                ''}"
            >
              {modeLabel(account.mode)}
            </span>
            {#if !account.active}
              <span class="text-xs font-normal text-text-muted"
                >({t("mollie.accounts.inactive")})</span
              >
            {/if}
          </h3>
          <p class="text-xs text-text-muted">
            {account.last_verified_at
              ? t("mollie.accounts.verified_at", { when: fmtDateTime(account.last_verified_at) })
              : t("mollie.accounts.never_verified")}
          </p>
          {#if account.status === "error" && account.last_error}
            <!-- Mollie's own untranslatable text, verbatim: it names the actual problem, and a
                 house sentence in its place would say less. -->
            <p class="mt-1 break-words text-xs text-red-600">
              {t("mollie.accounts.status.error")}: {account.last_error}
            </p>
          {/if}
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <form method="POST" action="?/verify" use:enhance={busy.wrap(`v-${account.id}`)}>
            <input type="hidden" name="account_id" value={account.id} />
            <Button
              variant="secondary"
              size="xs"
              loading={busy.is(`v-${account.id}`)}
              disabled={busy.active}
            >
              {t("mollie.accounts.verify")}
            </Button>
          </form>
          <!-- Edit and delete live in the ⋯ menu, never as bare buttons on a row header
               (docs/UX.md, "known mistakes"); the delete confirms. -->
          <ActionsMenu
            items={[
              {
                label: t("mollie.accounts.edit"),
                icon: Pencil,
                onclick: () => (editing = editing === account.id ? null : account.id),
              },
              {
                label: t("mollie.accounts.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => {
                  deleteTarget = account;
                  confirmDelete = true;
                },
              },
            ]}
          />
        </div>
      </div>

      {#if account.mode === "test"}
        <!-- Loud on purpose. A test key looks exactly like a working one everywhere else in the
             product: links are created, checkouts open, Mollie reports them paid — and no invoice
             is ever marked paid, because a test payment settles nothing. -->
        <p
          class="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
        >
          {t("mollie.accounts.mode_test_warning")}
        </p>
      {/if}

      <!-- What this credential can actually take. Mollie's own method ids, printed as they come:
           they are an observation of somebody else's dashboard, not a vocabulary we own, so
           translating them would be inventing a list we cannot keep true. -->
      <div class="mt-3 text-xs">
        <span class="text-text-muted">{t("mollie.accounts.methods")}:</span>
        {#if account.methods?.length}
          <span class="ml-1 inline-flex flex-wrap gap-1 align-middle">
            {#each account.methods as method (method)}
              <span
                class="rounded-full bg-surface px-2 py-0.5 font-medium text-text-muted ring-1 ring-inset ring-border"
              >
                {method}
              </span>
            {/each}
          </span>
        {:else}
          <span class="ml-1 text-text-muted">{t("mollie.accounts.methods_empty")}</span>
        {/if}
      </div>

      <!-- The notification URL. Read-only because it is derived from the org's domain and this
           account's own secret — the one thing on this screen nobody configures, and the one
           thing somebody may have to allow through an access proxy. -->
      <div class="mt-4 border-t border-border pt-3">
        <label class="mb-1 block text-sm font-medium text-text" for="webhook-{account.id}">
          {t("mollie.accounts.webhook_url")}
        </label>
        <div class="flex gap-2">
          <input
            id="webhook-{account.id}"
            readonly
            value={account.webhook_url}
            class="{inputClass} min-w-0 flex-1 bg-surface font-mono text-xs"
            onfocus={(e) => e.currentTarget.select()}
          />
          <Button
            type="button"
            variant="secondary"
            class="shrink-0"
            onclick={() => copyWebhookUrl(account)}
          >
            {copied === account.id
              ? t("mollie.accounts.webhook_copied")
              : t("mollie.accounts.webhook_copy")}
          </Button>
        </div>
        <p class="mt-1 text-xs text-text-muted">{t("mollie.accounts.webhook_url_help")}</p>
      </div>

      {#if editing === account.id}
        <form
          method="POST"
          action="?/update"
          use:enhance={busy.keep(`e-${account.id}`)}
          class="mt-4 grid gap-4 border-t border-border pt-4 sm:grid-cols-2"
        >
          <input type="hidden" name="account_id" value={account.id} />
          <div class="min-w-0">
            <label class={labelClass} for="name-{account.id}">{t("mollie.accounts.name")}</label>
            <input id="name-{account.id}" name="name" value={account.name} class={inputClass} />
          </div>
          {#if data.mayReadProviders}
            <div class="min-w-0">
              <label class={labelClass} for="provider-{account.id}">
                {t("mollie.accounts.provider")}
              </label>
              <select
                id="provider-{account.id}"
                name="provider_id"
                value={account.provider_id ?? ""}
                class={inputClass}
              >
                <option value="">—</option>
                {#each providers as provider (provider.id)}
                  <option value={provider.id}>{provider.name}</option>
                {/each}
              </select>
            </div>
          {/if}
          <div class="min-w-0">
            <label class={labelClass} for="key-{account.id}">{t("mollie.accounts.api_key")}</label>
            <input
              id="key-{account.id}"
              name="api_key"
              type="password"
              autocomplete="new-password"
              placeholder={account.api_key_configured
                ? t("mollie.accounts.api_key_configured")
                : ""}
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">{t("mollie.accounts.api_key_keep")}</p>
          </div>
          <!-- Stated, not offered: the mode is read from the key itself on every save, so there
               is deliberately no field here. Saying why is what stops an admin hunting for one. -->
          <div class="min-w-0">
            <span class={labelClass}>{t("mollie.accounts.mode")}</span>
            <p class="text-sm text-text">{modeLabel(account.mode)}</p>
            <p class="mt-1 text-xs text-text-muted">{t("mollie.accounts.mode_help")}</p>
          </div>
          <label class="flex items-center gap-2 text-sm text-text sm:col-span-2">
            <input
              type="checkbox"
              name="active"
              checked={account.active}
              class="rounded border-border"
            />
            {t("mollie.accounts.active")}
          </label>
          <div class="sm:col-span-2">
            <Button type="submit" loading={busy.is(`e-${account.id}`)} disabled={busy.active}>
              {t("common.save")}
            </Button>
          </div>
        </form>
      {/if}
    </article>
  {/each}
</section>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("mollie.accounts.delete")}
  message={t("mollie.accounts.delete_confirm", { name: deleteTarget?.name ?? "" })}
  action="?/delete"
  fields={{ account_id: deleteTarget?.id ?? "" }}
/>

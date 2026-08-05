<script lang="ts">
  /**
   * Instellingen → OXXA (issue #296).
   *
   * One row per reseller login, because an agency may hold more than one and a client may bring
   * theirs: nothing here ever picks a register for you, so the credential is a row, not a
   * setting (CLAUDE.md §10, the cloudflare rule applied to the registrar half).
   *
   * Two things this screen exists to say out loud. **Whether the credential works, and what it
   * is worth**: verify probes it and brings back the reseller balance, so "the register ran out
   * of credit and stopped renewing" is readable here instead of being invisible until a domain
   * lapses. And **which register rows match no schakl domain**: domains the agency is paying to
   * renew that no record, and therefore no invoice, knows about.
   *
   * The register reads on `oxxa.registrar.sync`, a different permission from this screen's own,
   * so a holder of only `oxxa.settings.manage` sees the credentials and no register at all —
   * rather than an empty table that lies about what is in it.
   */
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime, fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import type { AccountRead, RegistrarDomain } from "$lib/modules/oxxa/types";

  let { data, form } = $props();

  const accounts = $derived((data.accounts ?? []) as AccountRead[]);
  const register = $derived((data.register ?? []) as RegistrarDomain[]);
  const providers = $derived(data.providers ?? []);

  // Running a sync is `oxxa.registrar.sync`, not this screen's `settings.manage`: an admin who
  // may hold the credentials is not automatically someone who may act through them (#253 —
  // a control that renders without checking `can()`).
  const canSync = $derived(can(page.data.user, "oxxa.registrar.sync"));

  const busy = new InFlight();
  let adding = $state(false);
  let editing = $state<string | null>(null);
  let deleteTarget = $state<AccountRead | null>(null);
  let confirmDelete = $state(false);

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  const accountNames = $derived(new Map(accounts.map((a) => [a.id, a.name])));

  /** The reseller balance arrives as a decimal string; a non-number is simply not shown. */
  function funds(value: string | null | undefined): string {
    if (value === null || value === undefined || value === "") return "—";
    const amount = Number(value);
    return Number.isFinite(amount) ? fmtMoney(amount) : value;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("oxxa.settings.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("oxxa.settings.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("oxxa.settings.description")}</p>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}
{#if form?.saved}
  <p class="mb-4 text-sm text-green-600">{t("oxxa.account.saved")}</p>
{/if}
{#if form?.verify}
  <p class="mb-4 text-sm {form.verify.ok ? 'text-green-600' : 'text-red-600'}">
    {form.verify.ok ? t("oxxa.account.verified_ok") : t("oxxa.account.verified_failed")}
    {#if form.verify.error}<span class="text-text-muted"> {form.verify.error}</span>{/if}
  </p>
{/if}
{#if form?.sync}
  <p class="mb-4 text-sm text-green-600">
    {t("oxxa.account.synced_ok", {
      found: form.sync.found,
      matched: form.sync.matched,
      unmatched: form.sync.unmatched,
      drifted: form.sync.drifted,
    })}
  </p>
{/if}

<section class="max-w-4xl space-y-4">
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <h2 class="text-base font-medium text-text">{t("oxxa.title")}</h2>
    <Button type="button" variant="secondary" size="sm" onclick={() => (adding = !adding)}>
      {t("oxxa.account.add")}
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
          <label class={labelClass} for="new-name">{t("oxxa.account.name")}</label>
          <input id="new-name" name="name" required class={inputClass} />
        </div>
        <div class="min-w-0">
          <label class={labelClass} for="new-provider">{t("oxxa.account.provider")}</label>
          <select id="new-provider" name="provider_id" class={inputClass}>
            <option value="">—</option>
            {#each providers as provider (provider.id)}
              <option value={provider.id}>{provider.name}</option>
            {/each}
          </select>
        </div>
        <div class="min-w-0">
          <label class={labelClass} for="new-user">{t("oxxa.account.api_user")}</label>
          <input id="new-user" name="api_user" autocomplete="off" required class={inputClass} />
        </div>
        <div class="min-w-0">
          <label class={labelClass} for="new-password">{t("oxxa.account.api_password")}</label>
          <input
            id="new-password"
            name="api_password"
            type="password"
            autocomplete="new-password"
            required
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("oxxa.account.password_hint")}</p>
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
      {t("oxxa.account.empty")}
    </p>
  {/if}

  {#each accounts as account (account.id)}
    <article class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 class="truncate text-sm font-medium text-text">
            {account.name}
            {#if !account.active}
              <!-- Its own key, not "In gebruik" with a dash after it: a user-facing sentence is
                   never assembled from pieces (CLAUDE.md §8), and the assembled one said the
                   opposite of what it meant. -->
              <span class="text-xs text-text-muted">({t("oxxa.account.inactive")})</span>
            {/if}
          </h3>
          <p class="text-xs text-text-muted">
            {account.api_user}
            {#if account.provider_name}· {account.provider_name}{/if}
            · {account.last_verified_at
              ? t("oxxa.account.verified_at", { when: fmtDateTime(account.last_verified_at) })
              : t("oxxa.account.never")}
            {#if account.last_synced_at}
              · {t("oxxa.account.synced_at", { when: fmtDateTime(account.last_synced_at) })}
            {/if}
          </p>
          {#if account.status === "error" && account.last_error}
            <p class="mt-1 break-words text-xs text-red-600">
              {t("oxxa.status.error")}: {account.last_error}
            </p>
          {:else if account.status === "active"}
            <p class="mt-1 text-xs text-text-muted">{t("oxxa.status.active")}</p>
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
              {t("oxxa.account.verify")}
            </Button>
          </form>
          {#if canSync}
            <form method="POST" action="?/sync" use:enhance={busy.wrap(`s-${account.id}`)}>
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`s-${account.id}`)}
                disabled={busy.active}
              >
                {t("oxxa.account.sync")}
              </Button>
            </form>
          {/if}
          <!-- Edit and delete live in the ⋯ menu, never as bare buttons on a row header
               (docs/UX.md, "known mistakes"); the delete confirms. -->
          <ActionsMenu
            items={[
              {
                label: t("oxxa.account.edit"),
                icon: Pencil,
                onclick: () => (editing = editing === account.id ? null : account.id),
              },
              {
                label: t("oxxa.account.delete"),
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

      <!-- What the credential is worth and how far it reaches. A register out of credit stops
           renewing domains, and nothing else in schakl would ever mention it. -->
      <div class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
        <span>{t("oxxa.account.funds")}: {funds(account.funds_available)}</span>
        <span>{t("oxxa.account.tlds")}: {account.tld_count}</span>
        <span>{t("oxxa.account.domains")}: {account.domain_count}</span>
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
            <label class={labelClass} for="name-{account.id}">{t("oxxa.account.name")}</label>
            <input id="name-{account.id}" name="name" value={account.name} class={inputClass} />
          </div>
          <div class="min-w-0">
            <label class={labelClass} for="provider-{account.id}">
              {t("oxxa.account.provider")}
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
          <div class="min-w-0">
            <label class={labelClass} for="user-{account.id}">{t("oxxa.account.api_user")}</label>
            <input
              id="user-{account.id}"
              name="api_user"
              value={account.api_user}
              autocomplete="off"
              class={inputClass}
            />
          </div>
          <div class="min-w-0">
            <label class={labelClass} for="password-{account.id}">
              {t("oxxa.account.api_password")}
            </label>
            <input
              id="password-{account.id}"
              name="api_password"
              type="password"
              autocomplete="new-password"
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">{t("oxxa.account.password_hint")}</p>
          </div>
          <label class="flex items-center gap-2 text-sm text-text sm:col-span-2">
            <input
              type="checkbox"
              name="active"
              checked={account.active}
              class="rounded border-border"
            />
            {t("oxxa.account.active")}
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

<!-- The register ------------------------------------------------------------------------- -->
{#if data.mayReadRegister}
  <section class="mt-8 max-w-4xl">
    <h2 class="mb-3 text-base font-medium text-text">{t("oxxa.register.title")}</h2>

    <!-- A plain GET form: the filter is part of the address, so a filtered register can be
         linked, bookmarked and reloaded, and the list is narrowed by the API rather than in
         the browser (docs/PERFORMANCE.md). -->
    <form method="GET" class="flex flex-wrap items-end gap-3">
      <div class="min-w-0 flex-1">
        <label class={labelClass} for="register-q">{t("oxxa.register.search")}</label>
        <input id="register-q" name="q" value={data.q} class={inputClass} />
      </div>
      <label class="flex items-center gap-2 py-2 text-sm text-text">
        <input
          type="checkbox"
          name="unmatched"
          value="1"
          checked={data.unmatched}
          class="rounded border-border"
        />
        {t("oxxa.register.unmatched_only")}
      </label>
      <Button type="submit" variant="secondary">{t("common.search")}</Button>
    </form>
    <!-- What "niet gekoppeld" means, next to the control that filters on it — these are the
         rows worth a decision, not a property of the register as a whole. -->
    <p class="mb-3 mt-2 text-sm text-text-muted">{t("oxxa.register.unmatched_hint")}</p>

    {#if register.length === 0}
      <p class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted">
        {t("oxxa.register.empty")}
      </p>
    {:else}
      <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised p-5">
        <table class="w-full min-w-[32rem] text-sm">
          <thead class="text-left text-xs text-text-muted">
            <tr>
              <th class="py-1 pr-3 font-medium">{t("domains.name")}</th>
              <th class="py-1 pr-3 font-medium">{t("oxxa.panel.expires")}</th>
              <!-- The cell under this is the schakl *domain* the register row matched, not its
                   client — the register knows nothing about companies. -->
              <th class="py-1 pr-3 font-medium">{t("oxxa.register.linked")}</th>
              <th class="py-1 font-medium">{t("oxxa.panel.nameservers")}</th>
            </tr>
          </thead>
          <tbody>
            {#each register as entry (entry.id)}
              <tr class="border-t border-border align-top">
                <td class="min-w-0 break-all py-1.5 pr-3 text-text">
                  {entry.name}
                  {#if accounts.length > 1}
                    <span class="block text-xs text-text-muted">
                      {accountNames.get(entry.account_id) ?? ""}
                    </span>
                  {/if}
                </td>
                <td class="whitespace-nowrap py-1.5 pr-3 text-text-muted">
                  {entry.expires_on ? fmtNumericDate(entry.expires_on) : "—"}
                </td>
                <td class="py-1.5 pr-3">
                  {#if entry.domain_id}
                    <a class="text-brand hover:underline" href="/domains/{entry.domain_id}">
                      {entry.domain_name ?? entry.name}
                    </a>
                  {:else}
                    <span class="text-text-muted">—</span>
                  {/if}
                </td>
                <td class="min-w-0 break-all py-1.5 text-text-muted">
                  {entry.ns_observed?.join(", ") || "—"}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
        {#if data.registerTotal > register.length}
          <p class="mt-3 text-xs text-text-muted">
            {t("domains.count", { count: data.registerTotal })}
          </p>
        {/if}
      </div>
    {/if}
  </section>
{/if}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("oxxa.account.delete")}
  message={t("oxxa.account.delete_confirm", { name: deleteTarget?.name ?? "" })}
  action="?/delete"
  fields={{ account_id: deleteTarget?.id ?? "" }}
/>

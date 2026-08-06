<script lang="ts">
  /**
   * Instellingen → Cloudflare (epic #278).
   *
   * One row per Cloudflare account the agency works with, because an agency has its own and
   * some clients bring theirs — a single "the Cloudflare token" setting would have been wrong
   * on the first day of use.
   *
   * Two things this screen exists to say out loud. **What the token may do**: `verify` probes
   * it, so a missing scope reads as "Zones uitlezen: niet toegekend" here instead of as a 403
   * at a button three screens away. And **which zones are not matched to a domain**: an unknown
   * zone in a client's account is exactly what an agency taking over a setup wants to see.
   */
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { fmtDateTime } from "$lib/core/format";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { CAPABILITIES, type AccountRead, type ZoneRead } from "$lib/modules/cloudflare/types";

  let { data, form } = $props();

  const accounts = $derived((data.accounts ?? []) as AccountRead[]);
  const zones = $derived((data.zones ?? []) as ZoneRead[]);
  const providers = $derived(data.providers ?? []);

  const busy = new InFlight();
  let adding = $state(false);
  let editing = $state<string | null>(null);
  let deleteTarget = $state<AccountRead | null>(null);
  let confirmDelete = $state(false);

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  function zonesOf(accountId: string): ZoneRead[] {
    return zones.filter((zone) => zone.account_id === accountId);
  }

  function zoneStatus(value: string): string {
    const key = `cloudflare.zone_status.${value}`;
    const label = t(key);
    return label === key ? value : label;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.cloudflare.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.cloudflare.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.cloudflare.subtitle")}</p>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}
{#if form?.verify}
  <p class="mb-4 text-sm {form.verify.ok ? 'text-green-600' : 'text-red-600'}">
    {form.verify.ok ? t("cloudflare.accounts.verified") : t("cloudflare.accounts.verify_failed")}
    {#if form.verify.error}<span class="text-text-muted"> {form.verify.error}</span>{/if}
  </p>
  {#if form.verify.account_choices?.length}
    <p class="mb-4 text-sm text-amber-600">
      {t("cloudflare.accounts.choose_account")}
      <span class="text-text-muted">
        {form.verify.account_choices.map((a) => `${a.name} (${a.id})`).join(", ")}
      </span>
    </p>
  {/if}
{/if}
{#if form?.sync}
  <p class="mb-4 text-sm text-green-600">
    {t("cloudflare.accounts.synced", {
      zones: form.sync.zones_synced,
      matched: form.sync.zones_matched,
    })}
    <!-- The register decides which domains are invoiced (#298), so a sync says whether it
         answered — and an unread register is stated rather than left as a silent zero. -->
    <span class="mt-1 block text-text-muted">
      {form.sync.registrar_read
        ? t("cloudflare.accounts.synced_registrar", {
            total: form.sync.registrar_domains_synced,
            held: form.sync.registrar_domains_at_cloudflare,
            matched: form.sync.registrar_domains_matched,
          })
        : t("cloudflare.accounts.synced_registrar_none")}
    </span>
    <!-- What the Pages half found. A sync adopts hostnames already attached at Cloudflare, so
         this is where an agency sees its existing setup arrive — and where drift is named
         rather than silently resolved. -->
    <span class="mt-1 block text-text-muted">
      {t("cloudflare.accounts.synced_pages", {
        projects: form.sync.pages_projects_synced,
        domains: form.sync.pages_domains_synced,
        adopted: form.sync.pages_links_adopted,
      })}
    </span>
    {#if form.sync.pages_links_missing}
      <span class="mt-1 block text-amber-600">
        {t("cloudflare.accounts.synced_pages_missing", {
          missing: form.sync.pages_links_missing,
        })}
      </span>
    {/if}
    {#if form.sync.warnings?.includes("pages_domains_truncated")}
      <span class="mt-1 block text-amber-600">
        {t("cloudflare.accounts.synced_pages_truncated")}
      </span>
    {/if}
  </p>
{/if}

<section class="max-w-4xl space-y-4">
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <div>
      <h2 class="text-base font-medium text-text">{t("cloudflare.accounts.title")}</h2>
      <p class="text-sm text-text-muted">{t("cloudflare.accounts.intro")}</p>
    </div>
    <Button type="button" variant="secondary" size="sm" onclick={() => (adding = !adding)}>
      {t("cloudflare.accounts.add")}
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
          <label class={labelClass} for="new-name">{t("cloudflare.accounts.name")}</label>
          <input id="new-name" name="name" required class={inputClass} />
          <p class="mt-1 text-xs text-text-muted">{t("cloudflare.accounts.name_help")}</p>
        </div>
        <div class="min-w-0">
          <label class={labelClass} for="new-provider">{t("cloudflare.accounts.provider")}</label>
          <select id="new-provider" name="provider_id" class={inputClass}>
            <option value="">—</option>
            {#each providers as provider (provider.id)}
              <option value={provider.id}>{provider.name}</option>
            {/each}
          </select>
          <p class="mt-1 text-xs text-text-muted">{t("cloudflare.accounts.provider_help")}</p>
        </div>
        <div class="min-w-0 sm:col-span-2">
          <label class={labelClass} for="new-token">{t("cloudflare.accounts.token")}</label>
          <input
            id="new-token"
            name="api_token"
            type="password"
            autocomplete="new-password"
            required
            placeholder={t("cloudflare.accounts.token_placeholder")}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("cloudflare.accounts.token_help")}</p>
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
      {t("cloudflare.accounts.empty")}
    </p>
  {/if}

  {#each accounts as account (account.id)}
    <article class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 class="truncate text-sm font-medium text-text">
            {account.name}
            {#if !account.active}
              <span class="text-xs text-text-muted">({t("cloudflare.accounts.active")}: —)</span>
            {/if}
          </h3>
          <p class="text-xs text-text-muted">
            {account.cf_account_name ?? account.cf_account_id ?? "—"}
            · {account.last_verified_at
              ? t("cloudflare.accounts.verified_at", {
                  when: fmtDateTime(account.last_verified_at),
                })
              : t("cloudflare.accounts.never_verified")}
            {#if account.last_synced_at}
              · {t("cloudflare.accounts.synced_at", { when: fmtDateTime(account.last_synced_at) })}
            {/if}
          </p>
          <!-- A rejected token and a token merely missing one scope both leave text here, and
               they are not the same news: only the first is red, only the first is something
               the admin must fix before anything works. -->
          {#if account.last_error}
            <p
              class="mt-1 break-words text-xs {account.status === 'error'
                ? 'text-red-600'
                : 'text-text-muted'}"
            >
              {account.status === "error"
                ? t("cloudflare.accounts.status.error")
                : t("cloudflare.accounts.last_error")}: {account.last_error}
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
              {t("cloudflare.accounts.verify")}
            </Button>
          </form>
          <form method="POST" action="?/sync" use:enhance={busy.wrap(`s-${account.id}`)}>
            <input type="hidden" name="account_id" value={account.id} />
            <Button
              variant="secondary"
              size="xs"
              loading={busy.is(`s-${account.id}`)}
              disabled={busy.active}
            >
              {t("cloudflare.accounts.sync")}
            </Button>
          </form>
          <!-- Edit and delete live in the ⋯ menu, never as bare buttons on a row header
               (docs/UX.md, "known mistakes"); the delete confirms. -->
          <ActionsMenu
            items={[
              {
                label: t("common.edit"),
                icon: Pencil,
                onclick: () => (editing = editing === account.id ? null : account.id),
              },
              {
                label: t("common.delete"),
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

      <!-- What the token was observed to be allowed to do. A scoped token is not broken; it is
           scoped, and this is where an admin reads which permission to add. -->
      <div class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {#each CAPABILITIES as capability (capability)}
          <span class={account.capabilities?.[capability] ? "text-text" : "text-text-muted"}>
            {t(`cloudflare.capability.${capability}`)}:
            {account.capabilities?.[capability] ? "✓" : t("cloudflare.capability.missing")}
          </span>
        {/each}
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
            <label class={labelClass} for="name-{account.id}">
              {t("cloudflare.accounts.name")}
            </label>
            <input id="name-{account.id}" name="name" value={account.name} class={inputClass} />
          </div>
          <div class="min-w-0">
            <label class={labelClass} for="provider-{account.id}">
              {t("cloudflare.accounts.provider")}
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
            <label class={labelClass} for="cfid-{account.id}">
              {t("cloudflare.accounts.cf_account")}
            </label>
            <input
              id="cfid-{account.id}"
              name="cf_account_id"
              value={account.cf_account_id ?? ""}
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">{t("cloudflare.accounts.cf_account_help")}</p>
          </div>
          <div class="min-w-0">
            <label class={labelClass} for="token-{account.id}">
              {t("cloudflare.accounts.token")}
            </label>
            <input
              id="token-{account.id}"
              name="api_token"
              type="password"
              autocomplete="new-password"
              placeholder={account.token_configured
                ? t("cloudflare.accounts.token_configured")
                : ""}
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">{t("cloudflare.accounts.token_keep")}</p>
          </div>
          <label class="flex items-center gap-2 text-sm text-text sm:col-span-2">
            <input
              type="checkbox"
              name="active"
              checked={account.active}
              class="rounded border-border"
            />
            {t("cloudflare.accounts.active")}
          </label>
          <div class="sm:col-span-2">
            <Button type="submit" loading={busy.is(`e-${account.id}`)} disabled={busy.active}>
              {t("common.save")}
            </Button>
          </div>
        </form>
      {/if}

      <!-- The account's zones. Unmatched ones first: they are the ones that need a decision. -->
      <div class="mt-4 border-t border-border pt-3">
        <p class="mb-2 text-xs font-medium text-text">
          {t("cloudflare.zones.title")}
          <span class="font-normal text-text-muted">
            ({zonesOf(account.id).length === 1
              ? t("cloudflare.accounts.zone_count_one", { count: 1 })
              : t("cloudflare.accounts.zone_count_other", { count: zonesOf(account.id).length })})
          </span>
        </p>
        {#if zonesOf(account.id).length === 0}
          <p class="text-sm text-text-muted">{t("cloudflare.zones.empty")}</p>
        {:else}
          <div class="overflow-x-auto">
            <table class="w-full min-w-[28rem] text-sm">
              <thead class="text-left text-xs text-text-muted">
                <tr>
                  <th class="py-1 pr-3 font-medium">{t("cloudflare.dns.name")}</th>
                  <th class="py-1 pr-3 font-medium">{t("cloudflare.pages.status")}</th>
                  <th class="py-1 pr-3 font-medium">{t("cloudflare.zones.domain")}</th>
                  <th class="py-1"><span class="sr-only">…</span></th>
                </tr>
              </thead>
              <tbody>
                {#each zonesOf(account.id) as zone (zone.id)}
                  <tr class="border-t border-border">
                    <td class="min-w-0 break-all py-1.5 pr-3 text-text">{zone.name}</td>
                    <td class="py-1.5 pr-3 text-text-muted">{zoneStatus(zone.status)}</td>
                    <td class="py-1.5 pr-3">
                      {#if zone.domain_id}
                        <a class="text-brand hover:underline" href="/domains/{zone.domain_id}">
                          {zone.domain_name ?? zone.name}
                        </a>
                      {:else}
                        <span class="text-text-muted">{t("cloudflare.zones.unmatched")}</span>
                      {/if}
                    </td>
                    <td class="whitespace-nowrap py-1.5 text-right">
                      {#if zone.domain_id}
                        <form
                          method="POST"
                          action="?/unlinkZone"
                          use:enhance={busy.wrap(`u-${zone.id}`)}
                        >
                          <input type="hidden" name="zone_id" value={zone.id} />
                          <Button
                            variant="secondary"
                            size="xs"
                            loading={busy.is(`u-${zone.id}`)}
                            disabled={busy.active}
                          >
                            {t("cloudflare.zones.unlink")}
                          </Button>
                        </form>
                      {/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </div>
    </article>
  {/each}
</section>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("cloudflare.accounts.delete_confirm", { name: deleteTarget?.name ?? "" })}
  action="?/delete"
  fields={{ account_id: deleteTarget?.id ?? "" }}
/>

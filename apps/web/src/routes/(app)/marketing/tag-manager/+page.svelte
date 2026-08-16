<script lang="ts">
  /**
   * The linked Tag Manager containers. A directory, not a dashboard: pick a container and what
   * is in it is on its own page, where the wait for Google is expected.
   *
   * The one number on a card that is not decoration is the count of unpublished changes. A
   * change staged weeks ago and never published is the commonest way a client's tracking quietly
   * stops being what they were told it is, and nothing else on any screen surfaces it.
   */
  import { enhance } from "$app/forms";
  import { AlertTriangle, Plus, Tags } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";

  let { data, form } = $props();

  const busy = new InFlight();
  let linking = $state(false);
  let linkCompanyId = $state("");
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: linkCompanyId }),
  );

  const companyName = $derived((id: string | null | undefined) =>
    id ? (data.companies.find((c) => c.id === id)?.name ?? "") : "",
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(navLabel("gtm", t("nav.gtm")))}</title>
</svelte:head>

<div class="mb-4 flex items-start justify-between gap-4">
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("gtm", t("nav.gtm"))}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("gtm.subtitle")}</p>
  </div>
  {#if data.canManage}
    <button
      type="button"
      class="inline-flex shrink-0 items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
      onclick={() => (linking = true)}
    >
      <Plus size={15} aria-hidden="true" />
      {t("gtm.connect")}
    </button>
  {/if}
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-text">{t(form.error)}</p>
{/if}

{#if data.containers.length === 0}
  <div class="rounded-xl border border-border bg-surface-raised p-8 text-center">
    <Tags size={28} class="mx-auto mb-3 text-text-muted" aria-hidden="true" />
    <p class="text-sm text-text-muted">{t("gtm.empty")}</p>
    <p class="mx-auto mt-1 max-w-md text-xs text-text-muted">{t("gtm.empty_hint")}</p>
    {#if data.canManage}
      <button
        type="button"
        class="mt-3 inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
        onclick={() => (linking = true)}
      >
        <Plus size={15} aria-hidden="true" />
        {t("gtm.connect")}
      </button>
    {/if}
  </div>
{:else}
  <ul class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
    {#each data.containers as container (container.id)}
      <li>
        <a
          href="/marketing/tag-manager/{container.id}"
          class="block h-full rounded-xl border border-border bg-surface-raised p-4 hover:border-brand"
        >
          <div class="flex items-start gap-2">
            <Tags size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
            <div class="min-w-0 flex-1">
              <span class="block truncate text-sm font-medium text-text">
                {container.name || container.public_id}
              </span>
              <span class="mt-0.5 block truncate text-xs text-text-muted">
                {container.public_id}
                {#if container.company_id}· {companyName(container.company_id)}{/if}
              </span>
            </div>
          </div>
          <dl class="mt-3 grid grid-cols-3 gap-2 text-xs">
            <div>
              <dt class="text-text-muted">{t("gtm.live_version")}</dt>
              <dd class="text-text">
                {container.live_version_id ?? t("gtm.no_live_version")}
              </dd>
            </div>
            <div>
              <dt class="text-text-muted">{t("gtm.tag_count")}</dt>
              <dd class="text-text">{container.tag_count}</dd>
            </div>
            <div>
              <dt class="text-text-muted">{t("gtm.staged")}</dt>
              <dd class="text-text">
                {container.workspace_changes > 0
                  ? container.workspace_changes
                  : t("gtm.staged_none")}
              </dd>
            </div>
          </dl>
          {#if container.status === "error"}
            <!-- The glyph carries the state, not the colour: `text-brand` is gold on some
                 tenants and would read as a warning on every card. -->
            <span class="mt-3 flex items-start gap-1.5 text-xs text-text">
              <AlertTriangle size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
              <span class="min-w-0 break-words">
                {container.last_error ?? t("gtm.panel.error")}
              </span>
            </span>
          {:else if container.observed_at}
            <span class="mt-3 block text-xs text-text-muted">
              {t("gtm.checked", { when: fmtDateTime(container.observed_at) })}
            </span>
          {:else}
            <span class="mt-3 block text-xs text-text-muted">{t("gtm.never_checked")}</span>
          {/if}
        </a>
      </li>
    {/each}
  </ul>
{/if}

{#if data.canManage}
  <Modal bind:open={linking} title={t("gtm.connect")}>
    <!-- The mixed case, argued in place: it starts something new, so `reset: true` — and a
         success also closes the dialog, which a bare `clear()` cannot express. -->
    <form
      method="POST"
      action="?/link"
      use:enhance={busy.wrap("link", () => async ({ update, result }) => {
        await update({ reset: true });
        if (result.type === "success") linking = false;
      })}
      class="space-y-4"
    >
      <div>
        <label for="gtm-link-id" class="mb-1 block text-sm font-medium text-text">
          {t("settings.gtm.public_id")}
        </label>
        <input
          id="gtm-link-id"
          name="public_id"
          required
          placeholder="GTM-XXXXXXX"
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.gtm.public_id_hint")}</p>
      </div>
      <div>
        <label for="gtm-link-company" class="mb-1 block text-sm font-medium text-text">
          {t("settings.gtm.client")}
        </label>
        <Combobox
          items={companyPicker.live}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          name="company_id"
          id="gtm-link-company"
          bind:value={linkCompanyId}
          placeholder={t("settings.gtm.no_client")}
        />
      </div>
      <div class="flex justify-end gap-2">
        <Button type="button" variant="secondary" onclick={() => (linking = false)}>
          {t("common.cancel")}
        </Button>
        <Button type="submit" disabled={busy.active}>{t("settings.gtm.link")}</Button>
      </div>
    </form>
  </Modal>
{/if}

<script lang="ts">
  /**
   * Instellingen → Tag Manager: the containers, the workspace schakl writes in, and the switch
   * that stops every change to a client's live website at once.
   *
   * No credential field, because there is no credential: Tag Manager rides the per-user Google
   * grant. What the screen shows instead is whether *this* account carries it, with the connect
   * link beside it — the refusal an unconnected account meets is fixed by one reconnect, and a
   * screen that cannot say so leaves people looking for a password that does not exist.
   */
  import { enhance } from "$app/forms";
  import { AlertTriangle, ExternalLink } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";

  let { data, form } = $props();
  const settings = $derived(data.settings);

  const busy = new InFlight();

  // Empty is the agency's own container — a real state, and the one the client picker must not
  // require an answer for.
  let linkCompanyId = $state("");
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: linkCompanyId }),
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  const companyName = $derived((id: string | null | undefined) =>
    id ? (data.companies.find((c) => c.id === id)?.name ?? "") : "",
  );
</script>

<svelte:head>
  <title>{pageTitle(t("settings.gtm.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.gtm.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.gtm.subtitle")}</p>

{#if form?.error}
  <p class="mb-4 text-sm text-text">{t(form.error)}</p>
{/if}

{#if !data.connected}
  <!-- The connect link is *the* fix for every refusal on this screen, so it is at the top and
       it says what pressing it does. Incremental authorization: what was already granted stays. -->
  <section class="mb-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
    <p class="flex items-start gap-2 text-sm text-text">
      <AlertTriangle size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{t("gtm.reconnect_hint")}</span>
    </p>
    <a
      class="mt-3 inline-flex items-center gap-1 text-sm text-brand hover:underline"
      href="/api/v1/google/oauth/connect?include_tag_manager=true&next=/settings/gtm"
    >
      {t("gtm.reconnect")}
      <ExternalLink size={14} aria-hidden="true" />
    </a>
  </section>
{/if}

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- keep(): this edits settings that already exist, so a reset would rewind the workspace
       name the user just typed. -->
  <form method="POST" action="?/save" use:enhance={busy.keep()} class="space-y-5">
    <div class="flex items-start gap-2">
      <input
        id="gtm-writes-enabled"
        name="writes_enabled"
        type="checkbox"
        value="true"
        checked={settings?.writes_enabled ?? true}
        class="mt-0.5"
      />
      <label for="gtm-writes-enabled" class="text-sm text-text">
        {t("settings.gtm.writes_enabled")}
        <span class="mt-0.5 block text-xs text-text-muted">
          {t("settings.gtm.writes_enabled_hint")}
        </span>
      </label>
    </div>

    <div class="flex items-start gap-2 border-t border-border pt-5">
      <input
        id="gtm-own-workspace"
        name="own_workspace"
        type="checkbox"
        value="true"
        checked={settings?.own_workspace ?? true}
        class="mt-0.5"
      />
      <label for="gtm-own-workspace" class="text-sm text-text">
        {t("settings.gtm.own_workspace")}
        <span class="mt-0.5 block text-xs text-text-muted">
          {t("settings.gtm.own_workspace_hint")}
        </span>
      </label>
    </div>

    <div>
      <label for="gtm-workspace-name" class="mb-1 block text-sm font-medium text-text">
        {t("settings.gtm.workspace_name")}
      </label>
      <input
        id="gtm-workspace-name"
        name="workspace_name"
        value={settings?.workspace_name ?? "schakl"}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.gtm.workspace_name_hint")}</p>
    </div>

    <div class="flex items-center gap-3 border-t border-border pt-5">
      <Button type="submit" disabled={busy.active}>{t("common.save")}</Button>
      {#if form?.saved}<span class="text-sm text-text-muted">{t("common.saved")}</span>{/if}
    </div>
  </form>
</section>

<section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.gtm.containers")}</h2>
  <p class="mb-4 text-xs text-text-muted">{t("settings.gtm.containers_hint")}</p>

  {#if data.containers.length === 0}
    <p class="text-sm text-text-muted">{t("settings.gtm.no_containers")}</p>
  {:else}
    <ul class="mb-5 divide-y divide-border">
      {#each data.containers as container (container.id)}
        <li class="flex items-start gap-3 py-3">
          <div class="min-w-0 flex-1">
            <a
              href="/marketing/tag-manager/{container.id}"
              class="block truncate text-sm font-medium text-brand hover:underline"
            >
              {container.name || container.public_id}
            </a>
            <span class="mt-0.5 block truncate text-xs text-text-muted">
              {container.public_id}
              {#if container.company_id}· {companyName(container.company_id)}{/if}
              {#if !container.active}· {t("settings.gtm.inactive")}{/if}
            </span>
            {#if container.status === "error" && container.last_error}
              <span class="mt-1 block break-words text-xs text-text">{container.last_error}</span>
            {:else if container.last_verified_at}
              <span class="mt-1 block text-xs text-text-muted">
                {t("gtm.checked", { when: fmtDateTime(container.last_verified_at) })}
              </span>
            {/if}
          </div>
          <div class="flex shrink-0 gap-2">
            <!-- clear(): both start something rather than edit a field the user typed into. -->
            <form method="POST" action="?/verify" use:enhance={busy.clear()}>
              <input type="hidden" name="container_id" value={container.id} />
              <Button type="submit" variant="secondary" size="xs" disabled={busy.active}>
                {t("settings.gtm.verify")}
              </Button>
            </form>
            {#if container.active}
              <form method="POST" action="?/unlink" use:enhance={busy.clear()}>
                <input type="hidden" name="container_id" value={container.id} />
                <Button type="submit" variant="secondary" size="xs" disabled={busy.active}>
                  {t("settings.gtm.unlink")}
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
        <label for="gtm-public-id" class="mb-1 block text-sm font-medium text-text">
          {t("settings.gtm.public_id")}
        </label>
        <input
          id="gtm-public-id"
          name="public_id"
          required
          placeholder="GTM-XXXXXXX"
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.gtm.public_id_hint")}</p>
      </div>
      <div>
        <label for="gtm-company" class="mb-1 block text-sm font-medium text-text">
          {t("settings.gtm.client")}
        </label>
        <!-- The house type-ahead, not a native <select> over every company (docs/UX.md, #256).
             No `oncreate` — this form is for a container that already exists, and minting a
             client from a settings screen is not a gesture anybody standing here is making. -->
        <Combobox
          items={companyPicker.live}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          name="company_id"
          id="gtm-company"
          bind:value={linkCompanyId}
          placeholder={t("settings.gtm.no_client")}
        />
      </div>
    </div>
    <Button type="submit" disabled={busy.active || !data.connected}>
      {t("settings.gtm.link")}
    </Button>
  </form>
</section>

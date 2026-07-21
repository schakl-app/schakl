<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import PermissionMatrix from "$lib/core/roles/PermissionMatrix.svelte";

  let { data, form } = $props();

  const role = $derived(data.role);
  const isOwner = $derived(role.key === "owner");
  const catalog = $derived(data.permissionCatalog);
</script>

<svelte:head>
  <title>{pageTitle(t("settings.roles.title"))}</title>
</svelte:head>

<div class="mb-6">
  <div class="mt-1 flex flex-wrap items-center gap-2">
    <h1 class="text-xl font-semibold text-text">{role.name_i18n[data.locale] || role.key}</h1>
    {#if role.is_system}
      <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
        {t("settings.roles.system")}
      </span>
    {/if}
    <code class="rounded bg-surface px-1.5 py-0.5 text-xs text-text-muted">{role.key}</code>
  </div>
</div>

<!-- One form, one save button for the whole surface (docs/UX.md). The matrix's controls live
     inside it, so nothing is saved per field. `reset: false` is load-bearing: the matrix's
     marks are component state, and the default form reset reverts the DOM behind that
     state's back — the save then *looks* undone and the next save posts the old marks. -->
<form
  method="POST"
  action="?/save"
  id="role-form"
  use:enhance={() =>
    ({ update }) =>
      update({ reset: false })}
>
  <input type="hidden" name="is_owner" value={isOwner} />

  <div class="mb-5 rounded-xl border border-border bg-surface-raised p-5">
    <div class="grid gap-4 sm:grid-cols-2">
      <I18nTextField
        label={t("common.name_field")}
        basename="name"
        values={role.name_i18n}
        idPrefix="name"
        hint={false}
      />
      <I18nTextField
        label={t("settings.roles.description")}
        basename="description"
        values={role.description_i18n}
        idPrefix="description"
        hint={false}
      />
    </div>
    <p class="mt-1 text-xs text-text-muted">{t("common.translations_optional")}</p>
    {#if role.is_system}
      <p class="mt-3 text-xs text-text-muted">{t("settings.roles.system_hint")}</p>
    {/if}
  </div>

  <h2 class="mb-2 text-sm font-semibold text-text">{t("settings.roles.permissions")}</h2>
  {#if isOwner}
    <p
      class="mb-3 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-muted"
      data-testid="owner-locked"
    >
      {t("settings.roles.owner_locked")}
    </p>
  {:else}
    <p class="mb-3 text-xs text-text-muted">{t("settings.roles.scope_hint")}</p>
  {/if}

  {#if catalog}
    <PermissionMatrix {catalog} granted={role.permissions} disabled={isOwner} formId="role-form" />
  {/if}

  {#if form?.error}
    <p class="mt-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
  {/if}
  {#if form?.saved}
    <p class="mt-4 text-sm text-green-600">{t("settings.roles.saved")}</p>
  {/if}

  <div class="sticky bottom-0 mt-5 -mx-1 bg-surface/80 px-1 py-3 backdrop-blur">
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      data-testid="save-role"
    >
      {t("common.save")}
    </button>
  </div>
</form>

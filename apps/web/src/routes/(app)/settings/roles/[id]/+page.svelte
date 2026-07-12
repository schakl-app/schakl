<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import PermissionMatrix from "$lib/core/roles/PermissionMatrix.svelte";

  let { data, form } = $props();

  const role = $derived(data.role);
  const isOwner = $derived(role.key === "owner");
  const catalog = $derived(data.permissionCatalog);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.roles.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings/roles" class="text-sm text-text-muted hover:text-text"
    >{t("settings.roles.back")}</a
  >
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
     inside it, so nothing is saved per field. -->
<form method="POST" action="?/save" use:enhance id="role-form">
  <input type="hidden" name="is_owner" value={isOwner} />

  <div class="mb-5 rounded-xl border border-border bg-surface-raised p-5">
    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label for="name_nl" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.roles.name_nl")}</label
        >
        <input id="name_nl" name="name_nl" value={role.name_i18n.nl ?? ""} class={inputClass} />
      </div>
      <div>
        <label for="name_en" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.roles.name_en")}</label
        >
        <input id="name_en" name="name_en" value={role.name_i18n.en ?? ""} class={inputClass} />
      </div>
      <div>
        <label for="description_nl" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.roles.description")} (NL)</label
        >
        <input
          id="description_nl"
          name="description_nl"
          value={role.description_i18n.nl ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="description_en" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.roles.description")} (EN)</label
        >
        <input
          id="description_en"
          name="description_en"
          value={role.description_i18n.en ?? ""}
          class={inputClass}
        />
      </div>
    </div>
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

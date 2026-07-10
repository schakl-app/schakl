<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { moduleLabel } from "$lib/core/registry";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  const org = $derived(data.org);
  const host = $derived(org.custom_domain ?? `${org.slug}.${data.baseDomain}`);

  let confirmSuspend = $state(false);
  let confirmDelete = $state(false);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const sectionClass = "rounded-xl border border-border bg-surface-raised p-5";
  const sectionTitle = "text-sm font-semibold text-text";
  const buttonSecondary =
    "rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface";
  const statusClass: Record<string, string> = {
    active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    suspended: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    deleted: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  };
</script>

<svelte:head>
  <title>{org.name} · {t("instance.title")}</title>
</svelte:head>

<div class="mx-auto max-w-3xl space-y-6">
  <div>
    <a href="/instance" class="text-sm text-text-muted hover:text-text">
      ← {t("instance.title")}
    </a>
    <div class="mt-1 flex flex-wrap items-center gap-3">
      <h1 class="text-xl font-semibold text-text">{org.name}</h1>
      <span class="rounded-full px-2 py-0.5 text-xs font-medium {statusClass[org.status] ?? ''}">
        {t(`instance.status_${org.status}`)}
      </span>
    </div>
    <p class="mt-1 text-sm text-text-muted">{host}</p>
  </div>

  {#if form?.error && !form?.purgeError}
    <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
  {/if}

  <!-- Rename / re-slug -->
  <section class={sectionClass}>
    <h2 class={sectionTitle}>{t("instance.rename")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("instance.reslug_hint")}</p>
    <form
      method="POST"
      action="?/update"
      use:enhance={() =>
        async ({ update }) => {
          await update({ reset: false });
        }}
      class="mt-4 grid gap-4 sm:grid-cols-2"
    >
      <div>
        <label for="name" class="mb-1 block text-sm font-medium text-text">
          {t("instance.org_name")}
        </label>
        <input id="name" name="name" required maxlength="255" value={org.name} class={inputClass} />
      </div>
      <div>
        <label for="slug" class="mb-1 block text-sm font-medium text-text">
          {t("instance.slug")}
        </label>
        <input
          id="slug"
          name="slug"
          required
          maxlength="63"
          pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?"
          value={org.slug}
          class="{inputClass} font-mono"
        />
      </div>
      <div class="sm:col-span-2">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
        {#if form?.updated}<span class="ml-3 text-sm text-text-muted"
            >{t("settings.account.saved")}</span
          >{/if}
      </div>
    </form>
  </section>

  <!-- Modules -->
  <section class={sectionClass}>
    <h2 class={sectionTitle}>{t("settings.modules.title")}</h2>
    <form method="POST" action="?/modules" use:enhance class="mt-4 space-y-3">
      <div class="grid grid-cols-2 gap-2">
        {#each data.availableModules as moduleName (moduleName)}
          {@const isHub = moduleName === "companies"}
          <label class="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm">
            <input
              type="checkbox"
              name="modules"
              value={moduleName}
              checked={org.enabled_modules.includes(moduleName)}
              disabled={isHub}
              class="accent-brand"
            />
            {#if isHub}<input type="hidden" name="modules" value="companies" />{/if}
            {moduleLabel(moduleName)}
          </label>
        {/each}
      </div>
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
    </form>
  </section>

  <!-- Members + impersonation -->
  <section class={sectionClass}>
    <h2 class={sectionTitle}>{t("instance.members")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("instance.impersonate_hint")}</p>
    <div class="mt-3 divide-y divide-border">
      {#each org.members as member (member.user_id)}
        <div class="flex items-center justify-between gap-3 py-2">
          <div class="min-w-0">
            <p class="truncate text-sm font-medium text-text">
              {member.full_name || member.email}
            </p>
            <p class="truncate text-xs text-text-muted">
              {member.email} · {t(`roles.${member.role}`)}
            </p>
          </div>
          {#if org.status === "active" && member.is_active}
            <form method="POST" action="?/impersonate">
              <input type="hidden" name="user_id" value={member.user_id} />
              <button class={buttonSecondary}>{t("instance.impersonate")}</button>
            </form>
          {/if}
        </div>
      {:else}
        <p class="py-2 text-sm text-text-muted">{t("instance.no_members")}</p>
      {/each}
    </div>
  </section>

  <!-- Lifecycle -->
  <section class={sectionClass}>
    <h2 class={sectionTitle}>{t("instance.lifecycle")}</h2>
    <dl class="mt-3 grid gap-2 text-sm sm:grid-cols-2">
      <div>
        <dt class="text-text-muted">{t("instance.created_at")}</dt>
        <dd class="text-text">{fmtDateTime(org.created_at)}</dd>
      </div>
      <div>
        <dt class="text-text-muted">{t("instance.exported_at")}</dt>
        <dd class="text-text">{org.exported_at ? fmtDateTime(org.exported_at) : "—"}</dd>
      </div>
    </dl>
    <div class="mt-4 flex flex-wrap gap-2">
      <a href="/instance/{org.id}/export" class={buttonSecondary} data-sveltekit-preload-data="off">
        {t("instance.export")}
      </a>
      {#if org.status === "active"}
        <button type="button" class={buttonSecondary} onclick={() => (confirmSuspend = true)}>
          {t("instance.suspend")}
        </button>
      {:else}
        <form method="POST" action="?/activate" use:enhance>
          <button class={buttonSecondary}>{t("instance.activate")}</button>
        </form>
      {/if}
      {#if org.status !== "deleted"}
        <button
          type="button"
          class="rounded-lg border border-red-300 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950/40"
          onclick={() => (confirmDelete = true)}
        >
          {t("instance.soft_delete")}
        </button>
      {/if}
    </div>
  </section>

  <!-- Danger zone: hard delete, only for a soft-deleted org, export-gated API-side. -->
  {#if org.status === "deleted"}
    <section class="rounded-xl border border-red-300 p-5 dark:border-red-800">
      <h2 class="text-sm font-semibold text-red-700 dark:text-red-400">
        {t("instance.purge")}
      </h2>
      <p class="mt-1 text-xs text-text-muted">{t("instance.purge_hint")}</p>
      <form method="POST" action="?/purge" use:enhance class="mt-4 flex flex-wrap items-end gap-3">
        <div class="grow">
          <label for="confirm" class="mb-1 block text-sm font-medium text-text">
            {t("instance.purge_confirm", { slug: org.slug })}
          </label>
          <input id="confirm" name="confirm" required class="{inputClass} font-mono" />
        </div>
        <button
          class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
        >
          {t("instance.purge_action")}
        </button>
      </form>
      {#if form?.error && form?.purgeError}
        <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
    </section>
  {/if}
</div>

<ConfirmDialog
  bind:open={confirmSuspend}
  title={t("instance.suspend")}
  message={t("instance.suspend_confirm", { name: org.name })}
  action="?/suspend"
  confirmLabel={t("instance.suspend")}
/>
<ConfirmDialog
  bind:open={confirmDelete}
  title={t("instance.soft_delete")}
  message={t("instance.soft_delete_confirm", { name: org.name })}
  action="?/softDelete"
  confirmLabel={t("instance.soft_delete")}
/>

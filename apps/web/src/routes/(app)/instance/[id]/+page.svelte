<script lang="ts">
  import { applyAction, enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import { moduleLabel } from "$lib/core/registry";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

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
  <title>{pageTitle(`${org.name} · ${t("instance.title")}`)}</title>
</svelte:head>

<div class="mx-auto max-w-3xl space-y-6">
  <div>
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
      use:enhance={busy.wrap("update", () => async ({ update }) => {
        await update({ reset: false });
      })}
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
        <Button loading={busy.is("update")} disabled={busy.active}>
          {t("common.save")}
        </Button>
        {#if form?.updated}<span class="ml-3 text-sm text-text-muted"
            >{t("settings.account.saved")}</span
          >{/if}
      </div>
    </form>
  </section>

  <!-- Included e-mail (epic #199): may this org fall back to the instance's own transport? -->
  <section class={sectionClass}>
    <h2 class={sectionTitle}>{t("cloud.email_included.label")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("cloud.email_included.hint")}</p>
    <form
      method="POST"
      action="?/emailIncluded"
      use:enhance={busy.keep("emailIncluded")}
      class="mt-4 space-y-3"
    >
      <label class="flex items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          name="email_included"
          checked={org.email_included ?? true}
          class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
        />
        {t("cloud.email_included.enabled")}
      </label>
      <Button loading={busy.is("emailIncluded")} disabled={busy.active}>
        {t("common.save")}
      </Button>
      {#if form?.emailSaved}<span class="ml-3 text-sm text-text-muted"
          >{t("cloud.email_included.saved")}</span
        >{/if}
    </form>
  </section>

  <!-- Modules -->
  <section class={sectionClass}>
    <h2 class={sectionTitle}>{t("settings.modules.title")}</h2>
    <form
      method="POST"
      action="?/modules"
      use:enhance={busy.keep("modules")}
      class="mt-4 space-y-3"
    >
      <div class="grid grid-cols-2 gap-2">
        {#each data.availableModules as moduleName (moduleName)}
          {@const isHub = moduleName === "companies"}
          <label class="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm">
            <FormCheckbox
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
      <Button loading={busy.is("modules")} disabled={busy.active}>
        {t("common.save")}
      </Button>
    </form>
  </section>

  <!-- Custom domain (#292): operator-side configuration, ownership asserted + audited. -->
  <section class={sectionClass}>
    <h2 class={sectionTitle}>{t("instance.domain.title")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("instance.domain.hint")}</p>
    {#if data.domain && data.domain.stage !== "none"}
      <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p class="font-mono text-sm text-text">
            {data.domain.pending_domain ?? data.domain.custom_domain}
          </p>
          <p class="mt-0.5 text-xs text-text-muted">
            {t(`settings.domain.stage.${data.domain.stage}`)}
          </p>
        </div>
        <form method="POST" action="?/clearDomain" use:enhance={busy.wrap("clearDomain")}>
          <Button
            variant="secondary"
            size="sm"
            loading={busy.is("clearDomain")}
            disabled={busy.active}
          >
            {t("instance.domain.remove")}
          </Button>
        </form>
      </div>
      {#if data.domain.records.length}
        <div class="mt-3">
          <p class="text-xs font-medium text-text-muted">{t("instance.domain.records")}</p>
          <dl class="mt-1 space-y-1 font-mono text-xs text-text">
            {#each data.domain.records as record (record.purpose)}
              <div class="flex gap-2">
                <dt class="shrink-0 text-text-muted">{record.type}</dt>
                <dd class="break-all">{record.name} → {record.value}</dd>
              </div>
            {/each}
          </dl>
        </div>
      {/if}
    {/if}
    <form
      method="POST"
      action="?/setDomain"
      use:enhance={busy.keep("setDomain")}
      class="mt-4 grid gap-3 sm:grid-cols-2"
    >
      <input
        name="domain"
        placeholder="crm.klant.nl"
        aria-label={t("instance.domain.label")}
        class="{inputClass} font-mono"
      />
      <select name="mode" class={inputClass} aria-label={t("instance.domain.mode")}>
        <option value="activate">{t("instance.domain.mode_activate")}</option>
        <option value="claim">{t("instance.domain.mode_claim")}</option>
      </select>
      <div class="sm:col-span-2">
        <Button variant="secondary" loading={busy.is("setDomain")} disabled={busy.active}>
          {t("instance.domain.set")}
        </Button>
        {#if form?.domainSaved}<span class="ml-3 text-sm text-text-muted"
            >{t("instance.domain.saved")}</span
          >{/if}
      </div>
      {#if form?.error && form?.domainError}
        <p class="text-sm text-red-600 dark:text-red-400 sm:col-span-2">{t(form.error)}</p>
      {/if}
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
            <!-- Another org's host needs a script navigation, not a redirect: `form-action 'self'`
                 blocks a form submission that leaves this origin (#288). Impersonating a member of
                 *this* org stays a plain same-origin redirect from the action. -->
            <form
              method="POST"
              action="?/impersonate"
              use:enhance={() =>
                async ({ result }) => {
                  if (result.type === "success" && result.data?.handoffUrl) {
                    window.location.href = String(result.data.handoffUrl);
                    return;
                  }
                  await applyAction(result);
                }}
            >
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
        <form method="POST" action="?/activate" use:enhance={busy.wrap("activate")}>
          <Button variant="secondary" loading={busy.is("activate")} disabled={busy.active}>
            {t("instance.activate")}
          </Button>
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
      <form
        method="POST"
        action="?/purge"
        use:enhance={busy.wrap("purge")}
        class="mt-4 flex flex-wrap items-end gap-3"
      >
        <div class="grow">
          <label for="confirm" class="mb-1 block text-sm font-medium text-text">
            {t("instance.purge_confirm", { slug: org.slug })}
          </label>
          <input id="confirm" name="confirm" required class="{inputClass} font-mono" />
        </div>
        <Button variant="danger" loading={busy.is("purge")} disabled={busy.active}>
          {t("instance.purge_action")}
        </Button>
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

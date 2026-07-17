<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";

  let { data, form } = $props();

  let showCreate = $state(false);
  let createPlan = $state("trial");

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const statusClass: Record<string, string> = {
    active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    suspended: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    deleted: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  };
</script>

<svelte:head>
  <title>{t("cloud.console.title")}</title>
</svelte:head>

<div class="flex flex-wrap items-start justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("cloud.console.orgs_title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("cloud.console.orgs_subtitle")}</p>
  </div>
  <button
    type="button"
    name="new_org"
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = true)}
  >
    {t("instance.new_org")}
  </button>
</div>

<div class="mt-6 overflow-x-auto rounded-xl border border-border bg-surface-raised">
  <table class="w-full text-sm">
    <thead>
      <tr class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
        <th class="px-4 py-3">{t("instance.org")}</th>
        <th class="px-4 py-3">{t("instance.slug")}</th>
        <th class="px-4 py-3">{t("instance.status")}</th>
        <th class="px-4 py-3">{t("cloud.plan.label")}</th>
        <th class="px-4 py-3">{t("instance.domain")}</th>
        <th class="px-4 py-3"></th>
      </tr>
    </thead>
    <tbody>
      {#each data.orgs as org (org.id)}
        <tr class="border-b border-border last:border-0">
          <td class="px-4 py-3 font-medium text-text">{org.name}</td>
          <td class="px-4 py-3 font-mono text-text-muted">{org.slug}</td>
          <td class="px-4 py-3">
            <span class="rounded-full px-2 py-0.5 text-xs font-medium {statusClass[org.status] ?? ''}">
              {t(`instance.status_${org.status}`)}
            </span>
          </td>
          <td class="px-4 py-3 text-text-muted">
            {#if org.plan}
              {t(`cloud.plan.${org.plan}`)}
              {#if org.plan === "trial" && org.trial_ends_at}
                <span class="text-xs">({fmtNumericDate(org.trial_ends_at)})</span>
              {/if}
            {:else}
              —
            {/if}
          </td>
          <td class="px-4 py-3 text-text-muted">{org.custom_domain ?? "—"}</td>
          <td class="px-4 py-3 text-right">
            <a
              href="/console/orgs/{org.id}"
              class="text-sm font-medium text-brand hover:underline"
            >
              {t("common.edit")}
            </a>
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<h2 class="mt-10 text-sm font-semibold uppercase tracking-wide text-text-muted">
  {t("instance.audit")}
</h2>
<div class="mt-3 overflow-x-auto rounded-xl border border-border bg-surface-raised">
  {#if data.audit.length === 0}
    <p class="px-4 py-6 text-sm text-text-muted">{t("instance.audit_empty")}</p>
  {:else}
    <table class="w-full text-sm">
      <tbody>
        {#each data.audit as entry (entry.id)}
          <tr class="border-b border-border last:border-0">
            <td class="whitespace-nowrap px-4 py-2 text-text-muted">
              {fmtDateTime(entry.created_at)}
            </td>
            <td class="px-4 py-2 font-mono text-xs text-text">{entry.action}</td>
            <td class="px-4 py-2 text-text-muted">{entry.org_slug ?? ""}</td>
            <td class="px-4 py-2 text-text-muted">{entry.actor_email}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<Modal bind:open={showCreate} title={t("instance.new_org")}>
  <form
    method="POST"
    action="?/create"
    use:enhance={() =>
      ({ update, result }) => {
        void update().then(() => {
          if (result.type === "success") showCreate = false;
        });
      }}
    class="space-y-4"
  >
    <div>
      <label for="new-name" class="mb-1 block text-sm font-medium text-text">
        {t("instance.org_name")}
      </label>
      <input id="new-name" name="name" required maxlength="255" class={inputClass} />
    </div>
    <div>
      <label for="new-slug" class="mb-1 block text-sm font-medium text-text">
        {t("instance.slug")}
      </label>
      <input
        id="new-slug"
        name="slug"
        required
        maxlength="63"
        pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?"
        class="{inputClass} font-mono"
      />
    </div>
    <div>
      <label for="new-owner" class="mb-1 block text-sm font-medium text-text">
        {t("instance.owner_email")}
      </label>
      <input id="new-owner" name="owner_email" type="email" class={inputClass} />
      <p class="mt-1 text-xs text-text-muted">{t("instance.owner_email_hint")}</p>
    </div>
    <div>
      <label for="new-plan" class="mb-1 block text-sm font-medium text-text">
        {t("cloud.plan.label")}
      </label>
      <select id="new-plan" name="plan" bind:value={createPlan} class={inputClass}>
        <option value="trial">{t("cloud.plan.trial")}</option>
        <option value="standard">{t("cloud.plan.standard")}</option>
        <option value="unlimited">{t("cloud.plan.unlimited")}</option>
      </select>
      <p class="mt-1 text-xs text-text-muted">{t("cloud.plan.hint")}</p>
    </div>
    {#if createPlan === "trial"}
      <div>
        <label for="new-trial-days" class="mb-1 block text-sm font-medium text-text">
          {t("cloud.plan.trial_days")}
        </label>
        <input
          id="new-trial-days"
          name="trial_days"
          type="number"
          min="1"
          max="365"
          placeholder="14"
          class={inputClass}
        />
      </div>
    {/if}
    {#if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <button
      class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("common.save")}
    </button>
  </form>
</Modal>

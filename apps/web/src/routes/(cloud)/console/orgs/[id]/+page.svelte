<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  let { data, form } = $props();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const buttonClass =
    "rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface";

  const summary = $derived(data.locked ? data.summary : data.org);
  let planChoice = $derived(summary?.plan ?? "trial");
</script>

<svelte:head>
  <title>{summary?.name ?? t("cloud.console.title")}</title>
</svelte:head>

<a href="/console" class="text-sm font-medium text-brand hover:underline">
  ← {t("cloud.console.orgs_title")}
</a>

<div class="mt-3 flex flex-wrap items-start justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">{summary?.name}</h1>
    <p class="mt-1 font-mono text-sm text-text-muted">
      {summary?.custom_domain ?? `${summary?.slug}.${data.baseDomain}`}
      · {t(`instance.status_${summary?.status}`)}
    </p>
  </div>
</div>

{#if data.locked}
  <!-- Service PIN gate (epic #199): tenant data stays sealed until the org hands over a PIN. -->
  <div class="mt-6 max-w-md rounded-xl border border-border bg-surface-raised p-6">
    <h2 class="text-base font-semibold text-text">{t("cloud.pin.locked_title")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("cloud.pin.locked_hint")}</p>
    {#if data.access?.pin_pending}
      <p class="mt-2 text-sm text-amber-700 dark:text-amber-400">{t("cloud.pin.pending")}</p>
    {/if}
    <form method="POST" action="?/unlock" use:enhance class="mt-4 space-y-3">
      <div>
        <label for="pin" class="mb-1 block text-sm font-medium text-text">
          {t("cloud.pin.label")}
        </label>
        <input
          id="pin"
          name="pin"
          required
          inputmode="numeric"
          autocomplete="one-time-code"
          placeholder="0000-0000-0000"
          class="{inputClass} font-mono"
        />
      </div>
      {#if form?.error && form?.unlockError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <button
        class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("cloud.pin.unlock")}
      </button>
    </form>
  </div>
{:else if data.org}
  {#if data.access?.access_until}
    <p class="mt-2 text-xs text-text-muted">
      {t("cloud.pin.access_until", { until: fmtDateTime(data.access.access_until) })}
    </p>
  {/if}

  <!-- Members -->
  <section class="mt-6 overflow-x-auto rounded-xl border border-border bg-surface-raised">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
          <th class="px-4 py-3">{t("instance.members")}</th>
          <th class="px-4 py-3"></th>
          <th class="px-4 py-3"></th>
        </tr>
      </thead>
      <tbody>
        {#each data.org.members as member (member.user_id)}
          <tr class="border-b border-border last:border-0">
            <td class="px-4 py-3 text-text">{member.email}</td>
            <td class="px-4 py-3 text-text-muted">{member.role}</td>
            <td class="px-4 py-3 text-right">
              {#if member.is_active && data.org.status === "active"}
                <form method="POST" action="?/impersonate" use:enhance class="inline">
                  <input type="hidden" name="user_id" value={member.user_id} />
                  <button class="text-sm font-medium text-brand hover:underline">
                    {t("instance.impersonate")}
                  </button>
                </form>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </section>
{/if}

<!-- Plan (PIN-free: platform billing state, #200) -->
<section class="mt-6 max-w-md rounded-xl border border-border bg-surface-raised p-6">
  <h2 class="text-base font-semibold text-text">{t("cloud.plan.label")}</h2>
  <p class="mt-1 text-sm text-text-muted">{t("cloud.plan.hint")}</p>
  {#if summary?.plan === "trial" && summary?.trial_ends_at}
    <p class="mt-2 text-sm text-text">
      {t("cloud.plan.trial_ends", { date: fmtNumericDate(summary.trial_ends_at) })}
    </p>
  {/if}
  <form method="POST" action="?/plan" use:enhance class="mt-4 space-y-3">
    <select name="plan" bind:value={planChoice} class={inputClass}>
      <option value="trial">{t("cloud.plan.trial")}</option>
      <option value="standard">{t("cloud.plan.standard")}</option>
      <option value="unlimited">{t("cloud.plan.unlimited")}</option>
    </select>
    {#if planChoice === "trial"}
      <input
        name="trial_days"
        type="number"
        min="1"
        max="365"
        placeholder={t("cloud.plan.trial_days")}
        class={inputClass}
      />
    {/if}
    {#if form?.planSaved}
      <p class="text-sm text-emerald-700 dark:text-emerald-400">{t("cloud.plan.saved")}</p>
    {/if}
    <button class={buttonClass}>{t("common.save")}</button>
  </form>
</section>

<!-- Lifecycle (PIN-free: billing enforcement cannot depend on tenant consent) -->
<section class="mt-6 flex max-w-md flex-wrap gap-2">
  {#if summary?.status === "active"}
    <form method="POST" action="?/suspend" use:enhance>
      <button class={buttonClass}>{t("instance.suspend")}</button>
    </form>
  {:else if summary?.status === "suspended"}
    <form method="POST" action="?/activate" use:enhance>
      <button class={buttonClass}>{t("instance.activate")}</button>
    </form>
  {/if}
  {#if summary?.status !== "deleted"}
    <form method="POST" action="?/softDelete" use:enhance>
      <button class="{buttonClass} text-red-600 dark:text-red-400">
        {t("instance.soft_delete")}
      </button>
    </form>
  {:else}
    <form method="POST" action="?/activate" use:enhance>
      <button class={buttonClass}>{t("instance.activate")}</button>
    </form>
  {/if}
  {#if !data.locked}
    <a href="/console/orgs/{summary?.id}/export" class={buttonClass} data-sveltekit-preload-data="off">
      {t("instance.export")}
    </a>
  {/if}
</section>

{#if form?.error && !form?.unlockError && !form?.purgeError}
  <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

{#if summary?.status === "deleted"}
  <!-- Purge: soft-deleted + slug confirm + post-delete export, enforced API-side. -->
  <section class="mt-6 max-w-md rounded-xl border border-red-300 bg-surface-raised p-6 dark:border-red-900">
    <h2 class="text-base font-semibold text-red-700 dark:text-red-400">
      {t("instance.purge")}
    </h2>
    <p class="mt-1 text-sm text-text-muted">{t("instance.purge_hint")}</p>
    <form method="POST" action="?/purge" use:enhance class="mt-4 space-y-3">
      <input
        name="confirm"
        required
        placeholder={summary?.slug}
        class="{inputClass} font-mono"
      />
      {#if form?.error && form?.purgeError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <button class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("instance.purge")}
      </button>
    </form>
  </section>
{/if}

<script lang="ts">
  import { enhance } from "$app/forms";
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const buttonClass =
    "rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface";

  const summary = $derived(data.locked ? data.summary : data.org);
  let planChoice = $derived(summary?.plan ?? "trial");

  // A datetime from the API rendered back into the <input type="date"> value it came from.
  const asDateValue = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : "");
  const stageKey = $derived(
    ({
      warning: "cloud.lifecycle.stage_warning",
      suspended: "cloud.lifecycle.stage_suspended",
    })[summary?.lifecycle_stage ?? "active"] ?? "cloud.lifecycle.stage_active",
  );
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
    <form method="POST" action="?/unlock" use:enhance={busy.wrap("unlock")} class="mt-4 space-y-3">
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
      <Button class="w-full" loading={busy.is("unlock")} disabled={busy.active}>
        {t("cloud.pin.unlock")}
      </Button>
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
        <tr
          class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted"
        >
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
                <form
                  method="POST"
                  action="?/impersonate"
                  use:enhance={busy.wrap(`impersonate:${member.user_id}`)}
                  class="inline"
                >
                  <input type="hidden" name="user_id" value={member.user_id} />
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={busy.is(`impersonate:${member.user_id}`)}
                    disabled={busy.active}
                  >
                    {t("instance.impersonate")}
                  </Button>
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
  <form method="POST" action="?/plan" use:enhance={busy.keep("plan")} class="mt-4 space-y-3">
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
    <Button variant="secondary" loading={busy.is("plan")} disabled={busy.active}>
      {t("common.save")}
    </Button>
  </form>
</section>

<!-- End date (#199). PIN-free like the plan: platform state, not tenant content. -->
<section class="mt-6 max-w-md rounded-xl border border-border bg-surface-raised p-6">
  <h2 class="text-base font-semibold text-text">{t("cloud.lifecycle.end_date")}</h2>
  <p class="mt-1 text-sm text-text-muted">{t("cloud.lifecycle.end_date_hint")}</p>

  {#if summary?.ends_at}
    <dl class="mt-3 space-y-1 text-sm">
      <div class="flex justify-between gap-3">
        <dt class="text-text-muted">{t(stageKey)}</dt>
        <dd class="text-text">{fmtNumericDate(summary.ends_at)}</dd>
      </div>
      {#if summary.suspends_at}
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("cloud.lifecycle.suspends_on")}</dt>
          <dd class="text-text">{fmtNumericDate(summary.suspends_at)}</dd>
        </div>
      {/if}
      {#if summary.terminates_at}
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("cloud.lifecycle.deletes_on")}</dt>
          <dd class="font-medium text-rose-700 dark:text-rose-400">
            {fmtNumericDate(summary.terminates_at)}
          </dd>
        </div>
      {/if}
    </dl>
  {:else}
    <p class="mt-3 text-sm font-medium text-text">{t("cloud.lifecycle.unlimited")}</p>
  {/if}

  <form
    method="POST"
    action="?/lifecycle"
    use:enhance={busy.keep("lifecycle")}
    class="mt-4 space-y-3"
  >
    <input
      name="ends_on"
      type="date"
      value={asDateValue(summary?.ends_at)}
      class={inputClass}
      aria-label={t("cloud.lifecycle.end_date")}
    />
    <div class="grid grid-cols-2 gap-2">
      <input
        name="grace_days"
        type="number"
        min="0"
        max="3650"
        value={summary?.grace_days ?? ""}
        placeholder={t("cloud.lifecycle.grace_days")}
        aria-label={t("cloud.lifecycle.grace_days")}
        class={inputClass}
      />
      <input
        name="retention_days"
        type="number"
        min="0"
        max="3650"
        value={summary?.retention_days ?? ""}
        placeholder={t("cloud.lifecycle.retention_days")}
        aria-label={t("cloud.lifecycle.retention_days")}
        class={inputClass}
      />
    </div>
    <p class="text-xs text-text-muted">{t("cloud.lifecycle.inherit_hint")}</p>
    {#if form?.lifecycleSaved}
      <p class="text-sm text-emerald-700 dark:text-emerald-400">{t("cloud.lifecycle.saved")}</p>
    {/if}
    <Button variant="secondary" loading={busy.is("lifecycle")} disabled={busy.active}>
      {t("common.save")}
    </Button>
  </form>
</section>

<!-- Lifecycle (PIN-free: billing enforcement cannot depend on tenant consent) -->
<section class="mt-6 flex max-w-md flex-wrap gap-2">
  {#if summary?.status === "active"}
    <form method="POST" action="?/suspend" use:enhance={busy.wrap("suspend")}>
      <Button variant="secondary" loading={busy.is("suspend")} disabled={busy.active}>
        {t("instance.suspend")}
      </Button>
    </form>
  {:else if summary?.status === "suspended"}
    <form method="POST" action="?/activate" use:enhance={busy.wrap("activate")}>
      <Button variant="secondary" loading={busy.is("activate")} disabled={busy.active}>
        {t("instance.activate")}
      </Button>
    </form>
  {/if}
  {#if summary?.status !== "deleted"}
    <form method="POST" action="?/softDelete" use:enhance={busy.wrap("softDelete")}>
      <Button variant="danger-outline" loading={busy.is("softDelete")} disabled={busy.active}>
        {t("instance.soft_delete")}
      </Button>
    </form>
  {:else}
    <form method="POST" action="?/activate" use:enhance={busy.wrap("activate")}>
      <Button variant="secondary" loading={busy.is("activate")} disabled={busy.active}>
        {t("instance.activate")}
      </Button>
    </form>
  {/if}
  {#if !data.locked}
    <a
      href="/console/orgs/{summary?.id}/export"
      class={buttonClass}
      data-sveltekit-preload-data="off"
    >
      {t("instance.export")}
    </a>
  {/if}
</section>

{#if form?.error && !form?.unlockError && !form?.purgeError}
  <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

{#if summary?.status === "deleted"}
  <!-- Purge: soft-deleted + slug confirm + post-delete export, enforced API-side. -->
  <section
    class="mt-6 max-w-md rounded-xl border border-red-300 bg-surface-raised p-6 dark:border-red-900"
  >
    <h2 class="text-base font-semibold text-red-700 dark:text-red-400">
      {t("instance.purge")}
    </h2>
    <p class="mt-1 text-sm text-text-muted">{t("instance.purge_hint")}</p>
    <form method="POST" action="?/purge" use:enhance={busy.wrap("purge")} class="mt-4 space-y-3">
      <input name="confirm" required placeholder={summary?.slug} class="{inputClass} font-mono" />
      {#if form?.error && form?.purgeError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}
      <Button variant="danger" loading={busy.is("purge")} disabled={busy.active}>
        {t("instance.purge")}
      </Button>
    </form>
  </section>
{/if}

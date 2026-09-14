<script lang="ts">
  /**
   * Instellingen → Taken: the e-mail intake address.
   *
   * One address per org, read by the connected-mailbox feeds; a colleague who mails it gets a
   * task on their board, or a parked mail to finish by hand. The screen says when the last mail
   * arrived, because an address nothing has ever reached and an address that stopped working
   * look identical until something prints the difference (CLAUDE.md §10, the Timeon lesson).
   */
  import { enhance } from "$app/forms";
  import { fmtRelativeTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";

  let { data, form } = $props();
  const settings = $derived(data.settings);

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.tasks.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.tasks.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.tasks.subtitle")}</p>

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- keep(): this edits settings that already exist — a reset would blank the address the
       user just typed (docs/UX.md, "Saving must never blank the form"). -->
  <form method="POST" action="?/save" use:enhance={busy.keep()} class="space-y-5">
    <div>
      <h3 class="mb-1 text-sm font-semibold text-text">{t("settings.tasks.intake_heading")}</h3>
      <p class="mb-3 text-xs text-text-muted">{t("settings.tasks.intake_hint")}</p>

      <label for="intake-address" class="mb-1 block text-sm font-medium text-text">
        {t("settings.tasks.intake_address")}
      </label>
      <input
        id="intake-address"
        name="intake_address"
        type="email"
        value={settings?.intake_address ?? ""}
        placeholder={t("settings.tasks.intake_address_placeholder")}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">
        {#if settings?.intake_last_received_at}
          {t("settings.tasks.intake_last_received", {
            when: fmtRelativeTime(settings.intake_last_received_at),
            count: String(settings.intake_received_count ?? 0),
          })}
        {:else if settings?.intake_address}
          {t("settings.tasks.intake_never_received")}
        {/if}
      </p>
    </div>

    <div class="max-w-xs">
      <label for="intake-due-days" class="mb-1 block text-sm font-medium text-text">
        {t("settings.tasks.intake_default_due_days")}
      </label>
      <div class="flex items-center gap-2">
        <input
          id="intake-due-days"
          name="intake_default_due_days"
          type="number"
          min="0"
          max="365"
          value={settings?.intake_default_due_days ?? 1}
          class="{inputClass} w-24"
        />
        <span class="text-sm text-text-muted">{t("settings.tasks.intake_days")}</span>
      </div>
      <p class="mt-1 text-xs text-text-muted">
        {t("settings.tasks.intake_default_due_days_hint")}
      </p>
    </div>

    {#if form?.saved}
      <p class="text-sm text-green-600 dark:text-green-400">{t("settings.tasks.saved")}</p>
    {:else if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}

    <Button type="submit" loading={busy.active}>
      {t("common.save")}
    </Button>
  </form>
</section>

<section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <h3 class="mb-3 text-sm font-semibold text-text">{t("settings.tasks.how_heading")}</h3>
  <ol class="list-decimal space-y-2 pl-5 text-sm text-text">
    <li>{t("settings.tasks.how_subject")}</li>
    <li>{t("settings.tasks.how_directives")}</li>
    <li>{t("settings.tasks.how_forward")}</li>
    <li>
      {t("settings.tasks.how_ai")}
      <a href="/settings/ai" class="text-brand underline-offset-2 hover:underline">
        {t("settings.tasks.ai_link")}
      </a>
    </li>
    <li>{t("settings.tasks.how_parked")}</li>
  </ol>
</section>

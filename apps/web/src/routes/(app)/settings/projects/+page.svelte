<script lang="ts">
  /**
   * Instellingen → Projecten: the budget alert (global, not per project).
   *
   * One threshold for the whole agency, driving both halves of the nightly watch — the in-app
   * `project.budget_threshold` notification and the alert mail to the project's assignees —
   * so the bell and the mail can never disagree about what "almost spent" means.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";

  let { data, form } = $props();
  const settings = $derived(data.settings);

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.projects.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.projects.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.projects.subtitle")}</p>

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- keep(): this edits settings that already exist — a reset would rewind the threshold to
       the input's default (docs/UX.md, "Saving must never blank the form"). -->
  <form method="POST" action="?/save" use:enhance={busy.keep()} class="space-y-5">
    <div>
      <h3 class="mb-1 text-sm font-semibold text-text">
        {t("settings.projects.budget_alerts")}
      </h3>
      <p class="mb-3 text-xs text-text-muted">{t("settings.projects.budget_alerts_hint")}</p>

      <label class="flex items-center gap-2 text-sm text-text">
        <FormCheckbox
          name="budget_alert_emails"
          checked={settings?.budget_alert_emails ?? true}
          class="rounded border-border"
        />
        <span>{t("settings.projects.budget_alert_emails")}</span>
      </label>
    </div>

    <div class="max-w-xs">
      <label for="budget-alert-threshold" class="mb-1 block text-sm font-medium text-text">
        {t("settings.projects.budget_alert_threshold")}
      </label>
      <input
        id="budget-alert-threshold"
        name="budget_alert_threshold"
        type="number"
        min="5"
        max="100"
        value={settings?.budget_alert_threshold ?? 75}
        class={inputClass}
      />
      <p class="mt-1 text-xs text-text-muted">
        {t("settings.projects.budget_alert_threshold_hint")}
      </p>
    </div>

    {#if form?.saved}
      <p class="text-sm text-green-600 dark:text-green-400">{t("settings.projects.saved")}</p>
    {:else if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}

    <Button type="submit" loading={busy.active}>
      {t("common.save")}
    </Button>
  </form>
</section>

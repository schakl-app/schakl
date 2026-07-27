<script lang="ts">
  /**
   * Instellingen → Bedrijven: how this tenant numbers its clients (klantnummer).
   *
   * The format field previews live (see NumberFormatField) rather than describing its tokens in
   * prose — "what will my next client be called" is the only question this screen answers, and
   * a static hint makes you save to find out.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import NumberFormatField from "$lib/core/ui/NumberFormatField.svelte";

  let { data, form } = $props();

  const busy = new InFlight();
  let confirmBackfill = $state(false);
  let numberFormat = $state(data.settings?.client_number_format ?? "{seq:4}");

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const sectionClass = "rounded-xl border border-border bg-surface-raised p-5";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.companies.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-1 text-xl font-semibold text-text">{t("settings.companies.title")}</h1>
  <p class="text-sm text-text-muted">{t("settings.companies.subtitle")}</p>
</div>

{#if form?.saved}
  <p class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-text">
    {t("settings.companies.saved")}
  </p>
{/if}
{#if form?.numbered != null}
  <p class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-text">
    {t("settings.companies.backfilled", { count: form.numbered })}
  </p>
{/if}
{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<div class="max-w-3xl space-y-6">
  <section class={sectionClass}>
    <h2 class="mb-3 text-base font-semibold text-text">
      {t("settings.companies.numbering_heading")}
    </h2>
    <form
      method="POST"
      action="?/saveNumbering"
      use:enhance={busy.wrap("numbering")}
      class="grid gap-4 sm:grid-cols-2"
    >
      <div class="sm:col-span-2">
        <NumberFormatField
          id="client-number-format"
          name="client_number_format"
          label={t("settings.companies.number_format")}
          bind:value={numberFormat}
          nextSeq={data.settings?.client_number_next_seq ?? 1}
          class={inputClass}
        />
      </div>

      <div>
        <label for="next-seq" class="mb-1 block text-sm font-medium text-text">
          {t("settings.companies.next_seq")}
        </label>
        <input
          id="next-seq"
          name="client_number_next_seq"
          type="number"
          min="1"
          value={data.settings?.client_number_next_seq ?? 1}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.companies.next_seq_help")}</p>
      </div>

      <div class="space-y-3 sm:pt-6">
        <label class="flex items-start gap-2 text-sm text-text">
          <FormCheckbox
            name="client_number_auto"
            value="1"
            checked={data.settings?.client_number_auto ?? true}
            class="mt-0.5 rounded border-border"
          />
          <span>
            {t("settings.companies.auto")}
            <span class="mt-0.5 block text-xs text-text-muted">
              {t("settings.companies.auto_help")}
            </span>
          </span>
        </label>
        <label class="flex items-start gap-2 text-sm text-text">
          <FormCheckbox
            name="client_number_reset_yearly"
            value="1"
            checked={data.settings?.client_number_reset_yearly ?? false}
            class="mt-0.5 rounded border-border"
          />
          <span>
            {t("settings.companies.reset_yearly")}
            <span class="mt-0.5 block text-xs text-text-muted">
              {t("settings.companies.reset_yearly_help")}
            </span>
          </span>
        </label>
      </div>

      <div class="sm:col-span-2">
        <Button loading={busy.is("numbering")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  </section>

  <section class={sectionClass}>
    <h2 class="mb-1 text-base font-semibold text-text">
      {t("settings.companies.backfill_heading")}
    </h2>
    <p class="mb-3 text-sm text-text-muted">{t("settings.companies.backfill_help")}</p>
    <Button variant="secondary" onclick={() => (confirmBackfill = true)} disabled={busy.active}>
      {t("settings.companies.backfill")}
    </Button>
  </section>
</div>

<ConfirmDialog
  bind:open={confirmBackfill}
  title={t("settings.companies.backfill")}
  message={t("settings.companies.backfill_confirm")}
  action="?/backfill"
  confirmLabel={t("settings.companies.backfill")}
/>

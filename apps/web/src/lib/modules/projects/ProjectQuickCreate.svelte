<script lang="ts">
  /**
   * The new-project dialog behind a picker's "＋ … toevoegen" (docs/UX.md): the real field set —
   * name, client, billable — plus the tenant's own project custom fields, prefilled with what
   * was typed, posting to the caller's `createProject`-style action, which reports back through
   * `inlineCreated` so the asking picker auto-selects the new project.
   *
   * The client is a *default*, not a question re-asked (#247): a caller that already knows which
   * client the surrounding form is filing to passes it, and the picker still lets it be changed.
   *
   * The custom-field definitions are fetched here, on first open, instead of by every host page's
   * load: a dialog nobody opened must not cost a lookup on every render (docs/PERFORMANCE.md).
   * Creating is held until they land — a required field the form never drew would come back as a
   * validation error on a field the user was never shown.
   */
  import { enhance } from "$app/forms";

  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    name = "",
    companies = [],
    companyId = "",
    locale,
    action = "?/createProject",
    error = null,
    pickerSlot = "project",
  }: {
    open?: boolean;
    /** What was typed in the picker; prefills the name. */
    name?: string;
    /** The client roster for this dialog's own picker — the caller's, so it costs no fetch. */
    companies?: { value: string; label: string }[];
    /** Preselected client: what the surrounding form is already filing to (#247). */
    companyId?: string;
    locale: string;
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
    /** Echoed in `inlineCreated` so only the picker that asked auto-selects. */
    pickerSlot?: string;
  } = $props();

  // The caller's client is a default the user may change, so it is copied into local state —
  // re-copied on every open, because the surrounding form's client may have moved on since.
  let clientId = $state(companyId);
  $effect(() => {
    if (open) clientId = companyId;
  });

  let definitions = $state<CustomFieldDefinition[] | null>(null);
  let requested = false;
  $effect(() => {
    if (!open || requested) return;
    requested = true;
    void (async () => {
      const response = await fetch("/api/v1/custom-fields/definitions?entity_type=project", {
        headers: { accept: "application/json" },
      });
      definitions = response.ok ? await response.json() : [];
    })();
  });

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<Modal bind:open title={t("projects.new")}>
  {#key name + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") open = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <input type="hidden" name="slot" value={pickerSlot} />
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="qc-project-name" class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.name")}</label
          >
          <input id="qc-project-name" name="name" value={name} required class={inputClass} />
        </div>
        <div>
          <label for="qc-project-company" class="mb-1 block text-sm font-medium text-text"
            >{t("projects.field.company")}</label
          >
          <!-- Not optional: a project belongs to a client, and the API refuses one without.
               The caller's client is still only a *default* — this picker may change it. -->
          <Combobox
            items={companies}
            name="company_id"
            bind:value={clientId}
            id="qc-project-company"
            allowEmpty={false}
            placeholder={t("projects.field.company")}
          />
        </div>
      </div>
      <label class="flex items-center gap-2 text-sm font-medium text-text">
        <input
          name="billable_default"
          type="checkbox"
          checked
          class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
        />
        {t("projects.field.billable_default")}
      </label>
      {#if definitions?.length}
        <CustomFieldsForm {definitions} {locale} />
      {:else}
        <input type="hidden" name="custom" value={"{}"} />
      {/if}
      {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active} disabled={definitions === null || !clientId || busy.active}>
          {t("common.create")}
        </Button>
      </div>
    </form>
  {/key}
</Modal>

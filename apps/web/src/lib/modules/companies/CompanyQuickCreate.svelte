<script lang="ts">
  /**
   * The full new-client dialog behind a picker's "＋ … toevoegen" (#115, docs/UX.md): real
   * fields plus the tenant's custom-field definitions, prefilled with what was typed — never a
   * name-only stub. Posts to the caller's `createCompany`-style action
   * (`$lib/core/quickcreate.server.ts`), which reports back via `inlineCreated` / `qcError`.
   *
   * The definitions are the host page's when it loads them anyway (a client list already has
   * them in `data`); a caller that has none passes none and the dialog fetches its own on first
   * open, like `ProjectQuickCreate` does. That is what lets the ＋ live inside a component
   * rendered on pages that know nothing about clients — without it, every such host would have
   * to add a lookup to its load for a dialog most visits never open (docs/PERFORMANCE.md), and
   * the alternative, drawing the form without them, hands back a validation error on a required
   * field the user was never shown.
   */
  import { enhance } from "$app/forms";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { COMPANY_STATUSES } from "$lib/modules/companies/status";

  let {
    open = $bindable(false),
    name = "",
    definitions = null,
    locale,
    action = "?/createCompany",
    error = null,
    pickerSlot = "company",
  }: {
    open?: boolean;
    /** What was typed in the picker; prefills the name. */
    name?: string;
    /**
     * The host's already-loaded definitions. `null` (the default) means it has none to give —
     * not "there are none" — so the dialog fetches its own below. An explicit `[]` is a host
     * saying it looked and the tenant has defined none, and is taken at its word.
     */
    definitions?: CustomFieldDefinition[] | null;
    locale: string;
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
    /** Echoed in `inlineCreated` so only the picker that asked auto-selects (PartyPicker). */
    pickerSlot?: string;
  } = $props();

  // Fetched on first open, never on page load — a dialog nobody opened must not cost a lookup
  // on every render (docs/PERFORMANCE.md).
  let fetched = $state<CustomFieldDefinition[] | null>(null);
  let requested = false;
  $effect(() => {
    if (!open || definitions !== null || requested) return;
    requested = true;
    void (async () => {
      const response = await fetch("/api/v1/custom-fields/definitions?entity_type=company", {
        headers: { accept: "application/json" },
      });
      fetched = response.ok ? await response.json() : [];
    })();
  });
  const fields = $derived(definitions ?? fetched);

  const busy = new InFlight();
</script>

<Modal bind:open title={t("common.quick_create.company")}>
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
          <label for="qc-company-name" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.name")}</label
          >
          <input
            id="qc-company-name"
            name="name"
            value={name}
            required
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
        <div>
          <label for="qc-company-status" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.field.status")}</label
          >
          <select
            id="qc-company-status"
            name="status"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm"
          >
            {#each COMPANY_STATUSES as status (status)}
              <option value={status} selected={status === "active"}
                >{t(`companies.status.${status}`)}</option
              >
            {/each}
          </select>
        </div>
        <div class="sm:col-span-2">
          <label for="qc-company-website" class="mb-1 block text-sm font-medium text-text"
            >{t("companies.website")}</label
          >
          <input
            id="qc-company-website"
            name="website"
            placeholder="https://…"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
          />
        </div>
      </div>
      {#if fields?.length}
        <CustomFieldsForm definitions={fields} {locale} />
      {:else}
        <input type="hidden" name="custom" value={"{}"} />
      {/if}
      {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <!-- Held until the definitions land: a required field the form never drew would come
             back as a validation error on a field nobody was shown. -->
        <Button loading={busy.active} disabled={fields === null || busy.active}>
          {t("common.create")}
        </Button>
      </div>
    </form>
  {/key}
</Modal>

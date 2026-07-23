<script lang="ts">
  /**
   * The create-provider dialog behind a picker's "＋ … toevoegen" (#115, docs/UX.md). The kind
   * is fixed to the slot that asked (a registrar picker creates a registrar), the typed name is
   * prefilled. Providers have no custom fields, so this *is* the full create form — the same
   * fields Instellingen → Providers uses. Posts to the caller's `createProvider`-style action
   * (`$lib/core/quickcreate.server.ts`).
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    kind,
    name = "",
    action = "?/createProvider",
    error = null,
  }: {
    open?: boolean;
    kind: "registrar" | "dns" | "email" | "hosting";
    /** What was typed in the picker; prefills the name. */
    name?: string;
    action?: string;
    /** The page's `form?.qcError`. */
    error?: string | null;
  } = $props();

  const busy = new InFlight();
</script>

<Modal bind:open title={t("common.quick_create.provider", { kind: t(`providers.kind.${kind}`) })}>
  {#key kind + name + String(open)}
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") open = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <input type="hidden" name="kind" value={kind} />
      <div>
        <label for="qc-provider-name" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.providers.name")}</label
        >
        <input
          id="qc-provider-name"
          name="name"
          value={name}
          required
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (open = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>

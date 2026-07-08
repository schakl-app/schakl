<script lang="ts">
  /** Destructive-action confirmation: posts the given form action with hidden fields. */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    title,
    message,
    action,
    fields = {},
    confirmLabel,
  }: {
    open?: boolean;
    title: string;
    message: string;
    action: string;
    fields?: Record<string, string>;
    /** Text on the destructive confirm button; defaults to the shared "Delete" string. */
    confirmLabel?: string;
  } = $props();
</script>

<Modal bind:open {title}>
  <p class="text-sm text-neutral-600">{message}</p>
  <div class="mt-5 flex justify-end gap-2">
    <button
      type="button"
      class="rounded-lg border border-neutral-300 px-4 py-2 text-sm"
      onclick={() => (open = false)}>{t("common.cancel")}</button
    >
    <form
      method="POST"
      {action}
      use:enhance={() =>
        ({ update }) => {
          open = false;
          void update();
        }}
    >
      {#each Object.entries(fields) as [name, value] (name)}
        <input type="hidden" {name} {value} />
      {/each}
      <button
        class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {confirmLabel ?? t("common.delete")}
      </button>
    </form>
  </div>
</Modal>

<script lang="ts">
  /**
   * Action confirmation: posts the given form action with hidden fields.
   *
   * The confirm button defaults to a red "Delete" because deletes are the common case —
   * so any *other* action must pass `confirmLabel` (and, when it destroys nothing,
   * `variant="primary"`). A dialog that asks "issue this invoice?" over a red
   * "Delete" button reads as the opposite of what the button does.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    title,
    message,
    action,
    fields = {},
    confirmLabel,
    variant = "danger",
  }: {
    open?: boolean;
    title: string;
    message: string;
    action: string;
    fields?: Record<string, string>;
    /** Text on the confirm button; defaults to the shared "Delete" string. */
    confirmLabel?: string;
    /** Confirm-button style: red by default, `primary` when the action destroys nothing. */
    variant?: "danger" | "primary";
  } = $props();

  const busy = new InFlight();
</script>

<Modal bind:open {title}>
  <p class="text-sm text-text-muted">{message}</p>
  <div class="mt-5 flex justify-end gap-2">
    <button
      type="button"
      class="rounded-lg border border-border px-4 py-2 text-sm text-text"
      onclick={() => (open = false)}>{t("common.cancel")}</button
    >
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("", () => ({ update }) => {
        open = false;
        void update();
      })}
    >
      {#each Object.entries(fields) as [name, value] (name)}
        <input type="hidden" {name} {value} />
      {/each}
      <Button {variant} loading={busy.active}>
        {confirmLabel ?? t("common.delete")}
      </Button>
    </form>
  </div>
</Modal>

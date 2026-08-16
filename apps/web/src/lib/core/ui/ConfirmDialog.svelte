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
    consequences = [],
    action,
    fields = {},
    confirmLabel,
    variant = "danger",
  }: {
    open?: boolean;
    title: string;
    message: string;
    /**
     * What this action actually does, one clause per line, rendered as a list under the message.
     *
     * A sentence is enough for "delete this row?", and is not enough the moment an action has
     * effects the record it names does not show. Withdrawing a colleague's access deletes their
     * roles and takes their name off a thousand hours of work, and the dialog that asked
     * "Toegang van dit lid intrekken?" was accurate, complete as a question, and told the admin
     * none of it. Consequences belong *in the dialog*: nobody reads the manual at the moment
     * they are about to press a red button.
     */
    consequences?: string[];
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
  {#if consequences.length > 0}
    <ul class="mt-3 space-y-1.5 rounded-lg bg-surface px-3 py-2.5">
      {#each consequences as line (line)}
        <li class="flex gap-2 text-sm text-text-muted">
          <span aria-hidden="true" class="text-text-muted/60">•</span>
          <span>{line}</span>
        </li>
      {/each}
    </ul>
  {/if}
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

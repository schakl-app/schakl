<script lang="ts">
  /**
   * Action confirmation: posts the given form action with hidden fields.
   *
   * The confirm button defaults to a red "Delete" because deletes are the common case —
   * so any *other* action must pass `confirmLabel` (and, when it destroys nothing,
   * `variant="primary"`). A dialog that asks "issue this invoice?" over a red
   * "Delete" button reads as the opposite of what the button does.
   */
  import type { Snippet } from "svelte";

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
    acknowledge,
    confirmDisabled = false,
    children,
    onfailure,
    onsuccess,
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
    /**
     * A sentence the user has to tick before the confirm button works — for the one kind of
     * action that is *both* irreversible and something the record does not show the cost of
     * (deleting a numbered invoice: the number is gone from the run, and only the trail
     * remembers it). A consequences list is read; a box has to be pressed. Reset on every open.
     */
    acknowledge?: string;
    /**
     * Hold the confirm button while the host is still finding out what the action would do — a
     * dialog that reads the cost of a delete from the API must not offer the red button before
     * the answer is in (the client's delete dialog, docs/TRASH.md).
     */
    confirmDisabled?: boolean;
    /**
     * Extra controls inside the posting form — a choice between two ways of doing the thing,
     * say — so what the user picks travels with the confirmation instead of through a
     * second round of state the host has to mirror into `fields`.
     */
    children?: Snippet;
    /**
     * Called with the action's `error` key when it refuses, so the *host* can say so where the
     * user is looking. `page.form` is one slot shared by every action on a page, so a panel
     * cannot tell its own refusal from the edit form's — and a destructive control that reports
     * nothing at all reads as one that silently worked.
     */
    onfailure?: (errorKey: string) => void;
    /** Ran after a successful action, for a sibling the page load cannot refresh (a live
     *  listing that belongs to no `load`). */
    onsuccess?: () => void;
  } = $props();

  const busy = new InFlight();
  let acknowledged = $state(false);
  $effect(() => {
    // Every opening starts unticked: an acknowledgement is about *this* press.
    if (open) acknowledged = false;
  });
</script>

<Modal bind:open {title}>
  <form
    method="POST"
    {action}
    use:enhance={busy.wrap("", () => async ({ result, update }) => {
      open = false;
      if (result.type === "failure") {
        const key = (result.data as { error?: string } | undefined)?.error;
        if (key && onfailure) {
          onfailure(key);
          return;
        }
      }
      await update({ reset: false });
      onsuccess?.();
    })}
  >
    <p class="text-sm text-text-muted">{message}</p>
    {@render children?.()}
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
    {#if acknowledge}
      <label class="mt-4 flex items-start gap-2 text-sm text-text">
        <input
          type="checkbox"
          class="mt-0.5 h-4 w-4 rounded border-border accent-brand"
          bind:checked={acknowledged}
        />
        <span>{acknowledge}</span>
      </label>
    {/if}
    <div class="mt-5 flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (open = false)}>{t("common.cancel")}</button
      >
      {#each Object.entries(fields) as [name, value] (name)}
        <input type="hidden" {name} {value} />
      {/each}
      <Button
        {variant}
        loading={busy.active}
        disabled={confirmDisabled || (Boolean(acknowledge) && !acknowledged)}
      >
        {confirmLabel ?? t("common.delete")}
      </Button>
    </div>
  </form>
</Modal>

<script lang="ts">
  /**
   * Deleting a free-time pattern, and deciding what happens to the days it already placed.
   *
   * Not a plain `ConfirmDialog`, because this delete has a second question in it. A pattern is a
   * *rule*; the days it laid down are real approved leave the employee has planned around, so the
   * API keeps them by default (the FK is `SET NULL`). That default is right — a rule being removed
   * is no reason to wipe somebody's calendar — but on its own it made "verwijderen" a job half
   * done: a year of free Fridays left standing with nothing pointing at them and no way out but
   * cancelling each by hand. So the choice is asked here, with the count, and answered explicitly.
   *
   * Only days from today on are ever at stake; the past was taken and stays. Shared by the
   * employment wizard and the employee's own surface on /leave so the two cannot drift.
   */
  import { enhance } from "$app/forms";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    patternId,
    upcomingDays = 0,
    action = "?/deleteRecurring",
  }: {
    open?: boolean;
    patternId: string;
    /** How many of this pattern's days still stand from today on. */
    upcomingDays?: number;
    action?: string;
  } = $props();

  const busy = new InFlight();
  // Defaults to off: the destructive half of a destructive action is opt-in, and someone deleting
  // a pattern most often means "stop generating", not "clear the calendar".
  let withdraw = $state(false);
  // Re-arm per open, so a tick left on one pattern cannot carry to the next.
  $effect(() => {
    if (open) withdraw = false;
  });
</script>

<Modal bind:open title={t("leave.recurring.delete_title")}>
  <p class="text-sm text-text-muted">{t("leave.recurring.delete_confirm")}</p>

  {#if upcomingDays > 0}
    <label class="mt-4 flex items-start gap-2 rounded-lg border border-border p-3 text-sm">
      <input type="checkbox" bind:checked={withdraw} class="mt-0.5 h-4 w-4 rounded border-border" />
      <span>
        <span class="font-medium text-text">
          {t("leave.recurring.delete_withdraw", { count: upcomingDays })}
        </span>
        <span class="mt-0.5 block text-xs text-text-muted">
          {t("leave.recurring.delete_withdraw_hint")}
        </span>
      </span>
    </label>
  {:else}
    <p class="mt-3 text-xs text-text-muted">{t("leave.recurring.delete_no_days")}</p>
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
      <input type="hidden" name="id" value={patternId} />
      <input type="hidden" name="withdraw_days" value={String(withdraw)} />
      <Button variant="danger" loading={busy.active}>{t("common.delete")}</Button>
    </form>
  </div>
</Modal>

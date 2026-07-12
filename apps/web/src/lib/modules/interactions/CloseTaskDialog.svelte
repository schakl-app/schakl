<script lang="ts">
  /**
   * Close the linked task *with* this contact moment (#157) — GitHub's "close with comment",
   * but the comment is a contactmoment (a call, a meeting, an approved email). Posts to the
   * host page's `?/closeTaskWithInteraction`; the API stores the designation, validates the
   * linkage and records it on the task's trail.
   *
   * The org's terminal statuses load when the dialog opens: exactly one → a plain confirm;
   * several → a radio pick.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";

  import type { InteractionItem } from "./format";

  let {
    interaction,
    onsaved,
  }: {
    interaction: InteractionItem;
    onsaved?: () => void;
  } = $props();

  interface StatusDef {
    id: string;
    key: string;
    name: string;
    is_terminal: boolean;
  }

  let terminal = $state<StatusDef[]>([]);
  let picked = $state("");
  let loading = $state(true);
  let error = $state("");

  $effect(() => {
    void loadStatuses();
  });

  async function loadStatuses() {
    loading = true;
    try {
      const response = await fetch("/api/v1/tasks/statuses", {
        headers: { accept: "application/json" },
      });
      const statuses: StatusDef[] = response.ok ? await response.json() : [];
      terminal = statuses.filter((status) => status.is_terminal);
      picked = terminal[0]?.key ?? "";
    } catch {
      error = "errors.server";
    } finally {
      loading = false;
    }
  }
</script>

<form
  method="POST"
  action="?/closeTaskWithInteraction"
  class="space-y-4"
  use:enhance={() =>
    async ({ result, update }) => {
      if (result.type === "failure") {
        error = String(result.data?.error ?? "errors.validation");
        return;
      }
      error = "";
      await update({ reset: false });
      onsaved?.();
    }}
>
  <input type="hidden" name="task_id" value={interaction.task_id} />
  <input type="hidden" name="interaction_id" value={interaction.id} />

  <p class="text-sm text-text-muted">
    {t("interactions.close_task_message", {
      subject: interaction.subject || t(`interactions.kind.${interaction.kind}`),
    })}
  </p>

  {#if loading}
    <p class="text-sm text-text-muted">{t("common.loading")}</p>
  {:else if terminal.length === 0}
    <p class="text-sm text-red-600">{t("interactions.close_task_no_terminal")}</p>
  {:else if terminal.length > 1}
    <fieldset class="space-y-1.5">
      <legend class="mb-1 text-sm font-medium text-text">
        {t("interactions.close_task_pick_status")}
      </legend>
      {#each terminal as status (status.id)}
        <label class="flex items-center gap-2 text-sm text-text">
          <input type="radio" name="status" value={status.key} bind:group={picked} />
          {status.name}
        </label>
      {/each}
    </fieldset>
  {:else}
    <input type="hidden" name="status" value={picked} />
  {/if}

  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end">
    <button
      type="submit"
      disabled={loading || terminal.length === 0}
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
    >
      {t("interactions.close_task")}
    </button>
  </div>
</form>

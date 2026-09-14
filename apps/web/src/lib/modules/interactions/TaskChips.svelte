<script lang="ts">
  /**
   * Which tasks a contactmoment is about: chips plus a type-ahead — `ContactChips`' shape, one
   * link over. One email often answers three tickets, and a single picker made the logger pick
   * a winner while the other two tasks' timelines quietly omitted the conversation.
   *
   * The roster posts as one hidden `task_ids` field (comma-separated, chip order) and the
   * **first** chip is the lead: it is what the API mirrors onto `task_id`, what derives the
   * client, and what the close-task / fill-in offers below the pickers read — so it is posted
   * as `task_id` too, which is also what keeps every reader of that field (the close action, an
   * older build) seeing exactly what it saw. Clicking another chip promotes it, colour alone
   * marks it (docs/UX.md).
   *
   * The options are the host's: it runs the client → project → task cascade and hands the live
   * and retired halves in, and hears about the lead through `onpick` so it can backfill the
   * levels above — the picker itself knows nothing about projects.
   */
  import { X } from "@lucide/svelte";

  import { fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import type { PickerOption } from "$lib/core/picker";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  import { loadSeriesOptions, type TaskOption } from "./lookups";

  let {
    picked = $bindable([]),
    items,
    archived = [],
    archivedLabel,
    labels = {},
    name = "task_ids",
    id = "task-chips",
    formId,
    placeholder,
    onpick,
    oncreate,
    onseries,
  }: {
    /** The picked ids, in chip order; the first is the lead. */
    picked?: string[];
    /** The live options the cascade allows right now. */
    items: PickerOption[];
    /** The finished ones, behind the search wearing their status (`$lib/core/picker`). */
    archived?: PickerOption[];
    archivedLabel?: string;
    /** Titles for stored chips the options do not carry (an edit on a row outside the lookup). */
    labels?: Record<string, string | null | undefined>;
    name?: string;
    id?: string;
    /** Associate the posted fields with a <form id=…> they do not sit inside. */
    formId?: string;
    placeholder?: string;
    /** The lead changed (a first pick, or a promotion): the host backfills project and client. */
    onpick?: (leadId: string) => void;
    /** Inline-create (docs/UX.md): the host owns the dialog, so it takes what was typed. */
    oncreate?: (query: string) => void;
    /**
     * A series was unfolded (`Combobox.onexpand`): here are its other occurrences, as the
     * host's own option shape, so its cascade can answer for a nested pick — which project it
     * belongs to, whose it is — without the host fetching anything of its own. The host keeps
     * them `nested`, and `lookups.splitLinkOptions` keeps them out of the top-level rows.
     */
    onseries?: (rows: TaskOption[]) => void;
  } = $props();

  let comboValue = $state("");
  // What an unfolded occurrence is called once it is a chip: the series' title with its date,
  // because "21 nov" alone is a chip nobody can read back and the title alone is its parent's.
  let seriesLabels = $state<Record<string, string>>({});

  /** The rest of a series, for the picker to nest under its current occurrence. */
  async function expandSeries(item: PickerOption): Promise<PickerOption[]> {
    const rows = await loadSeriesOptions(item.value);
    onseries?.(rows);
    seriesLabels = {
      ...seriesLabels,
      ...Object.fromEntries(
        rows.map((row) => [
          row.value,
          t("tasks.picker.series_occurrence", {
            title: item.label,
            date: row.due_date ? fmtPeriod(row.due_date) : "",
          }),
        ]),
      ),
    };
    return rows.map((row) => ({
      value: row.value,
      label: row.due_date ? fmtPeriod(row.due_date) : row.label,
    }));
  }

  const candidates = $derived(items.filter((option) => !picked.includes(option.value)));
  const retired = $derived(archived.filter((option) => !picked.includes(option.value)));

  function label(taskId: string): string {
    return (
      items.find((option) => option.value === taskId)?.label ??
      archived.find((option) => option.value === taskId)?.label ??
      seriesLabels[taskId] ??
      labels[taskId] ??
      taskId
    );
  }

  function pick(taskId: string) {
    comboValue = "";
    if (!taskId || picked.includes(taskId)) return;
    picked = [...picked, taskId];
    if (picked.length === 1) onpick?.(taskId);
  }

  function remove(taskId: string) {
    const wasLead = picked[0] === taskId;
    picked = picked.filter((p) => p !== taskId);
    if (wasLead && picked[0]) onpick?.(picked[0]);
  }

  function promote(taskId: string) {
    if (!picked.includes(taskId)) return;
    picked = [taskId, ...picked.filter((p) => p !== taskId)];
    onpick?.(taskId);
  }
</script>

<input type="hidden" {name} value={picked.join(",")} form={formId} />
<input type="hidden" name="task_id" value={picked[0] ?? ""} form={formId} />

<div class="space-y-2">
  {#if picked.length > 0}
    <ul class="flex flex-wrap gap-2">
      {#each picked as taskId, index (taskId)}
        <li
          class="relative inline-flex items-center gap-1.5 rounded-full py-1 pl-2.5 pr-1.5 text-sm
            transition-colors
            {index === 0
            ? 'bg-brand/10 text-brand ring-1 ring-inset ring-brand/30'
            : 'bg-surface text-text hover:bg-brand/10 hover:text-brand hover:ring-1 hover:ring-inset hover:ring-brand/30'}"
        >
          {#if index > 0}
            <button
              type="button"
              class="absolute inset-0 cursor-pointer rounded-full"
              title={t("interactions.tasks.make_lead")}
              aria-label={t("interactions.tasks.make_lead")}
              onclick={() => promote(taskId)}
            ></button>
          {/if}
          <span class="pointer-events-none font-medium">
            {label(taskId)}
            {#if index === 0}
              <span class="sr-only">({t("interactions.tasks.lead")})</span>
            {/if}
          </span>
          <button
            type="button"
            class="relative rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
            title={t("interactions.tasks.remove")}
            aria-label={t("interactions.tasks.remove")}
            onclick={() => remove(taskId)}><X size={14} /></button
          >
        </li>
      {/each}
    </ul>
  {/if}

  <Combobox
    items={candidates}
    archived={retired}
    {archivedLabel}
    name="_task_pick"
    bind:value={comboValue}
    {id}
    placeholder={placeholder ??
      (picked.length ? t("interactions.tasks.add_more") : t("interactions.field.task_placeholder"))}
    allowEmpty={false}
    onselect={pick}
    keepOpenOnSelect
    {oncreate}
    onexpand={expandSeries}
  />
</div>

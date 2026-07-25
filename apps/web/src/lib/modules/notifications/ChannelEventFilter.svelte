<script lang="ts">
  /**
   * Which event types route to an external channel (#245, completing #17's `event_filter`).
   *
   * The stored filter is a list of event types; the empty list means "all events" (the API's own
   * meaning). So the picker is a two-state toggle — All events / Only selected — over the same
   * grouped vocabulary the preference matrix shows. Switching to "Only selected" seeds every event
   * checked (start from everything, remove what you don't want); unchecking down to nothing lands
   * back on "All events", which is honest: an empty filter *is* all events.
   */
  import { t } from "$lib/core/i18n";

  let {
    events,
    value,
    onchange,
  }: {
    /** The full event-type vocabulary, in display order (from the matrix). */
    events: string[];
    /** The current filter; [] = all events. */
    value: string[];
    onchange: (selected: string[]) => void;
  } = $props();

  const mode = $derived(value.length === 0 ? "all" : "selected");

  // Cluster by module prefix, exactly like the preference matrix — presentational only.
  const groups = $derived.by(() => {
    const out: { key: string; events: string[] }[] = [];
    for (const event of events) {
      const key = event.split(".")[0];
      const last = out[out.length - 1];
      if (last && last.key === key) last.events.push(event);
      else out.push({ key, events: [event] });
    }
    return out;
  });

  function setMode(next: "all" | "selected"): void {
    if (next === "all") onchange([]);
    else if (value.length === 0) onchange([...events]);
  }

  function toggle(event: string, on: boolean): void {
    onchange(on ? [...value, event] : value.filter((e) => e !== event));
  }
</script>

<div class="space-y-3">
  <span class="block text-sm text-text">{t("settings.notifications.channel_events")}</span>
  <div class="flex flex-wrap gap-2">
    {#each [["all", "channel_events_all"], ["selected", "channel_events_selected"]] as [m, key] (m)}
      <button
        type="button"
        class="rounded-lg border px-3 py-1.5 text-sm {mode === m
          ? 'border-brand bg-surface text-brand'
          : 'border-border text-text hover:border-brand'}"
        aria-pressed={mode === m}
        onclick={() => setMode(m as "all" | "selected")}
      >
        {t(`settings.notifications.${key}`)}
      </button>
    {/each}
  </div>

  {#if mode === "selected"}
    <div class="space-y-3 rounded-lg border border-border p-3">
      {#each groups as group (group.key)}
        <div>
          <p class="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
            {t(`notifications.group.${group.key}`)}
          </p>
          <div class="grid gap-1 sm:grid-cols-2">
            {#each group.events as event (event)}
              <label class="flex items-center gap-2 text-sm text-text">
                <input
                  type="checkbox"
                  checked={value.includes(event)}
                  onchange={(e) => toggle(event, e.currentTarget.checked)}
                />
                {t(`notifications.event_label.${event}`)}
              </label>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

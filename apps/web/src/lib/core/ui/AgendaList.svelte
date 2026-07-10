<script lang="ts">
  /**
   * Mobile-first per-day agenda list, shared by every calendar view (docs/UX.md: small
   * screens get a list instead of cramped grid cells).
   */
  import { fmtLongDay } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { labelDotClass } from "$lib/core/ui/colors";
  import type { CalendarEvent } from "$lib/core/registry";

  let {
    days,
    eventsByDay,
    today,
    showEmptyDays = false,
    emptyMessage = t("calendar.empty"),
  }: {
    days: string[];
    eventsByDay: Record<string, CalendarEvent[]>;
    today: string;
    /** Show every day (week/day views) instead of only days that have events (month view). */
    showEmptyDays?: boolean;
    /** Shown when no day in `days` has any event. */
    emptyMessage?: string;
  } = $props();

  const listDays = $derived(showEmptyDays ? days : days.filter((d) => d in eventsByDay));
</script>

<div class="space-y-3">
  {#each listDays as day (day)}
    <section class="rounded-xl border border-border bg-surface-raised p-4">
      <h3
        class="mb-2 text-xs font-semibold capitalize {day === today
          ? 'text-brand'
          : 'text-text-muted'}"
      >
        {fmtLongDay(day)}
      </h3>
      {#if (eventsByDay[day] ?? []).length > 0}
        <ul class="space-y-1.5">
          {#each eventsByDay[day] ?? [] as event (event.id + day)}
            <li>
              {#if event.kind === "holiday"}
                <!-- Nobody's absence, so it links nowhere and wears no leave colour (#47). -->
                <span class="flex items-center gap-2 text-sm text-text-muted">
                  <span class="h-2 w-2 shrink-0 rounded-full border border-dashed border-border"
                  ></span>
                  <span class="truncate">{event.title}</span>
                </span>
              {:else}
                <a
                  href={event.href ?? "#"}
                  class="flex items-center gap-2 text-sm text-text {event.tentative
                    ? 'opacity-60'
                    : ''}"
                >
                  <span class="h-2 w-2 shrink-0 rounded-full {labelDotClass(event.color)}"></span>
                  <span class="truncate">{event.title}</span>
                  {#if event.tentative}
                    <span class="text-xs text-text-muted">{t("calendar.tentative")}</span>
                  {/if}
                </a>
              {/if}
            </li>
          {/each}
        </ul>
      {:else}
        <p class="text-sm text-text-muted">{t("calendar.day_empty")}</p>
      {/if}
    </section>
  {:else}
    <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
      {emptyMessage}
    </p>
  {/each}
</div>

<script lang="ts">
  import { ChevronLeft, ChevronRight, SlidersHorizontal } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { CALENDAR_VIEWS, shiftDate, weekGrid, type CalendarView } from "$lib/core/calendar";
  import { dateLocale, fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import type { CalendarEvent } from "$lib/core/registry";
  import { labelDotClass } from "$lib/core/ui/colors";
  import DayCalendar from "$lib/core/ui/DayCalendar.svelte";
  import MonthCalendar from "$lib/core/ui/MonthCalendar.svelte";
  import WeekCalendar from "$lib/core/ui/WeekCalendar.svelte";
  import YearCalendar from "$lib/core/ui/YearCalendar.svelte";

  let { data, form } = $props();

  const SHORTCUT_KEY: Record<CalendarView, string> = { day: "d", week: "w", month: "m", year: "y" };

  let saveForm: HTMLFormElement | undefined = $state();
  let saveViewInput: HTMLInputElement | undefined = $state();

  // Drag-to-reschedule (#106): the drop fills this hidden form and submits it to ?/moveEvent;
  // the enhance round-trip reloads the feeds, so the chip lands where the API says it landed.
  let moveForm: HTMLFormElement | undefined = $state();
  let moveSource: HTMLInputElement | undefined = $state();
  let moveId: HTMLInputElement | undefined = $state();
  let moveDelta: HTMLInputElement | undefined = $state();

  function onEventMove(event: CalendarEvent, deltaDays: number) {
    if (!moveForm || !moveSource || !moveId || !moveDelta || !event.sourceKey) return;
    moveSource.value = event.sourceKey;
    moveId.value = event.id;
    moveDelta.value = String(deltaDays);
    moveForm.requestSubmit();
  }

  function hrefFor(view: CalendarView, date: string = data.date): string {
    return `?view=${view}&date=${date}`;
  }

  // Fire-and-forget: never let the page load itself react to the URL and silently overwrite
  // a visitor's stored pref when they open someone else's shared `?view=...` link.
  function persistView(view: CalendarView) {
    if (!saveForm || !saveViewInput) return;
    saveViewInput.value = view;
    saveForm.requestSubmit();
  }

  function switchView(view: CalendarView) {
    if (view === data.view) return;
    persistView(view);
    void goto(hrefFor(view), { keepFocus: true });
  }

  const navLabel = $derived.by(() => {
    const d = new Date(data.date + "T00:00:00Z");
    if (data.view === "day") {
      return new Intl.DateTimeFormat(dateLocale(), {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      }).format(d);
    }
    if (data.view === "week") {
      const days = weekGrid(data.date);
      return `${fmtDayMonth(days[0])} – ${fmtDayMonth(days[days.length - 1])}`;
    }
    if (data.view === "month") {
      return new Intl.DateTimeFormat(dateLocale(), {
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      }).format(d);
    }
    return data.date.slice(0, 4);
  });

  // Feed visibility (#121): the menu doubles as the legend. Toggling posts the *whole* hidden
  // list and the enhance reload refetches the range — a hidden feed then costs no API call.
  let sourcesOpen = $state(false);
  let sourcesRoot: HTMLElement | undefined = $state();
  let sourcesForm: HTMLFormElement | undefined = $state();
  // Writable derived: follows the stored pref until a toggle overwrites it mid-save.
  let hiddenDraft = $derived(data.sourceOptions.filter((s) => s.hidden).map((s) => s.key));
  function toggleSource(key: string) {
    hiddenDraft = hiddenDraft.includes(key)
      ? hiddenDraft.filter((k) => k !== key)
      : [...hiddenDraft, key];
    // Submit on the next tick so the hidden inputs carry the fresh list.
    setTimeout(() => sourcesForm?.requestSubmit(), 0);
  }

  function onkeydown(e: KeyboardEvent) {
    const target = e.target as HTMLElement | null;
    if (
      target &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable)
    ) {
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    const key = e.key.toLowerCase();
    if (key === "t") {
      void goto(hrefFor(data.view, data.today), { keepFocus: true });
      return;
    }
    const view = (Object.keys(SHORTCUT_KEY) as CalendarView[]).find((v) => SHORTCUT_KEY[v] === key);
    if (view) switchView(view);
  }

  const navButton =
    "flex items-center rounded-lg border border-border p-2 text-text-muted hover:border-brand hover:text-brand";
</script>

<svelte:window
  {onkeydown}
  onclick={(e) => {
    if (sourcesOpen && sourcesRoot && !sourcesRoot.contains(e.target as Node)) {
      sourcesOpen = false;
    }
  }}
/>

<svelte:head>
  <title>{pageTitle(t("calendar.title"))}</title>
</svelte:head>

<!-- The sibling nav link/keyboard shortcut already triggers the authoritative navigation to
     the new URL, so this save is a fire-and-forget no-op enhance: it must never also
     invalidateAll (that would race a redundant reload). -->
<form
  method="POST"
  action="?/saveView"
  bind:this={saveForm}
  use:enhance={() => async () => {}}
  class="hidden"
>
  <input type="hidden" name="view" bind:this={saveViewInput} />
</form>

<!-- The drop side of drag-to-reschedule (#106). Default enhance: success reloads the feeds,
     failure surfaces below as an ordinary form error. -->
<form method="POST" action="?/moveEvent" bind:this={moveForm} use:enhance class="hidden">
  <input type="hidden" name="source" bind:this={moveSource} />
  <input type="hidden" name="id" bind:this={moveId} />
  <input type="hidden" name="delta" bind:this={moveDelta} />
</form>

<!-- Feed visibility (#121). Default enhance: the reload refetches only the visible feeds. -->
<form method="POST" action="?/saveSources" bind:this={sourcesForm} use:enhance class="hidden">
  {#each hiddenDraft as key (key)}
    <input type="hidden" name="hidden" value={key} />
  {/each}
</form>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("calendar.title")}</h1>
  <div class="flex flex-wrap items-center gap-2" data-sveltekit-preload-data="hover">
    <a
      href={hrefFor(data.view, shiftDate(data.date, data.view, -1))}
      class={navButton}
      aria-label={t("calendar.nav.previous")}
    >
      <ChevronLeft size={16} />
    </a>
    <a
      href={hrefFor(data.view, data.today)}
      class="rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand hover:text-brand"
      title={t("calendar.view.shortcut_hint", { label: t("calendar.today"), key: "t" })}
    >
      {t("calendar.today")}
    </a>
    <a
      href={hrefFor(data.view, shiftDate(data.date, data.view, 1))}
      class={navButton}
      aria-label={t("calendar.nav.next")}
    >
      <ChevronRight size={16} />
    </a>
    <span class="ml-1 text-sm font-medium capitalize text-text">{navLabel}</span>

    <div
      class="ml-2 flex overflow-hidden rounded-lg border border-border"
      role="group"
      aria-label={t("calendar.view.label")}
    >
      {#each CALENDAR_VIEWS as view (view)}
        <a
          href={hrefFor(view)}
          onclick={() => persistView(view)}
          title={t("calendar.view.shortcut_hint", {
            label: t(`calendar.view.${view}`),
            key: SHORTCUT_KEY[view],
          })}
          class="px-2.5 py-1.5 text-xs font-medium {view === data.view
            ? 'bg-brand text-white'
            : 'text-text-muted hover:bg-surface'}"
        >
          {t(`calendar.view.${view}`)}
        </a>
      {/each}
    </div>

    <!-- Show/hide each feed, personal + persisted (#121); the checkboxes double as the legend. -->
    {#if data.sourceOptions.length > 0}
      <div class="relative" bind:this={sourcesRoot}>
        <button
          type="button"
          class={navButton}
          aria-expanded={sourcesOpen}
          aria-haspopup="true"
          aria-label={t("calendar.sources.label")}
          title={t("calendar.sources.label")}
          onclick={() => (sourcesOpen = !sourcesOpen)}
        >
          <SlidersHorizontal size={16} />
        </button>
        {#if sourcesOpen}
          <div
            class="absolute right-0 z-30 mt-1 w-56 rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
          >
            <p class="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted">
              {t("calendar.sources.label")}
            </p>
            {#each data.sourceOptions as source (source.key)}
              <label
                class="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm text-text hover:bg-surface"
              >
                <input
                  type="checkbox"
                  checked={!hiddenDraft.includes(source.key)}
                  onchange={() => toggleSource(source.key)}
                  class="h-3.5 w-3.5 rounded border-border"
                />
                <span class="h-2 w-2 rounded-full {labelDotClass(source.color)}"></span>
                {t(source.labelKey)}
              </label>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>
</div>

{#if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400"
  >
    {t(form.error)}
  </p>
{/if}

{#if data.view === "day"}
  <DayCalendar date={data.date} events={data.events} today={data.today} />
{:else if data.view === "week"}
  <WeekCalendar
    days={weekGrid(data.date)}
    events={data.events}
    today={data.today}
    onmove={onEventMove}
  />
{:else if data.view === "month"}
  <MonthCalendar
    month={data.date.slice(0, 7)}
    events={data.events}
    today={data.today}
    onmove={onEventMove}
  />
{:else}
  <YearCalendar
    year={data.date.slice(0, 4)}
    aggregates={data.aggregates ?? {}}
    today={data.today}
  />
{/if}

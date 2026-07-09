<script lang="ts">
  import { ChevronLeft, ChevronRight } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { CALENDAR_VIEWS, shiftDate, weekGrid, type CalendarView } from "$lib/core/calendar";
  import { dateLocale, fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DayCalendar from "$lib/core/ui/DayCalendar.svelte";
  import MonthCalendar from "$lib/core/ui/MonthCalendar.svelte";
  import WeekCalendar from "$lib/core/ui/WeekCalendar.svelte";
  import YearCalendar from "$lib/core/ui/YearCalendar.svelte";

  let { data } = $props();

  const SHORTCUT_KEY: Record<CalendarView, string> = { day: "d", week: "w", month: "m", year: "y" };

  let saveForm: HTMLFormElement | undefined = $state();
  let saveViewInput: HTMLInputElement | undefined = $state();

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
    "flex items-center rounded-lg border border-neutral-300 p-2 text-neutral-500 hover:border-brand hover:text-brand";
</script>

<svelte:window {onkeydown} />

<svelte:head>
  <title>{t("calendar.title")}</title>
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

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-neutral-900">{t("calendar.title")}</h1>
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
      class="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:border-brand hover:text-brand"
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
    <span class="ml-1 text-sm font-medium capitalize text-neutral-700">{navLabel}</span>

    <div
      class="ml-2 flex overflow-hidden rounded-lg border border-neutral-300"
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
            : 'text-neutral-500 hover:bg-neutral-50'}"
        >
          {t(`calendar.view.${view}`)}
        </a>
      {/each}
    </div>
  </div>
</div>

{#if data.view === "day"}
  <DayCalendar date={data.date} events={data.events} today={data.today} />
{:else if data.view === "week"}
  <WeekCalendar days={weekGrid(data.date)} events={data.events} today={data.today} />
{:else if data.view === "month"}
  <MonthCalendar month={data.date.slice(0, 7)} events={data.events} today={data.today} />
{:else}
  <YearCalendar
    year={data.date.slice(0, 4)}
    aggregates={data.aggregates ?? {}}
    today={data.today}
  />
{/if}

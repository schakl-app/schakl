<script lang="ts">
  import { ChevronLeft, ChevronRight } from "@lucide/svelte";

  import { addMonths, monthOf } from "$lib/core/calendar";
  import { dateLocale } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import MonthCalendar from "$lib/core/ui/MonthCalendar.svelte";

  let { data } = $props();

  const monthLabel = $derived(
    new Intl.DateTimeFormat(dateLocale(), {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(data.month + "-01T00:00:00Z")),
  );

  const navButton =
    "flex items-center rounded-lg border border-neutral-300 p-2 text-neutral-500 hover:border-brand hover:text-brand";
</script>

<svelte:head>
  <title>{t("calendar.title")}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-neutral-900">{t("calendar.title")}</h1>
  <div class="flex items-center gap-2" data-sveltekit-preload-data="hover">
    <a
      href="?month={addMonths(data.month, -1)}"
      class={navButton}
      aria-label={t("calendar.previous")}
    >
      <ChevronLeft size={16} />
    </a>
    <a
      href="?month={monthOf(data.today)}"
      class="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:border-brand hover:text-brand"
    >
      {t("calendar.today")}
    </a>
    <a
      href="?month={addMonths(data.month, 1)}"
      class={navButton}
      aria-label={t("calendar.next")}
    >
      <ChevronRight size={16} />
    </a>
    <span class="ml-1 text-sm font-medium capitalize text-neutral-700">{monthLabel}</span>
  </div>
</div>

<MonthCalendar month={data.month} events={data.events} today={data.today} />

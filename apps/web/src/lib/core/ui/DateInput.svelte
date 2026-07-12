<script lang="ts">
  /**
   * European date field. Browsers format native <input type="date"> after the *browser*
   * locale (often US), so this renders its own dd-mm-yyyy text input and posts the ISO
   * value through a hidden input. Typing accepts 7-7-2026, 07/07/2026 and 07.07.26.
   *
   * The calendar button opens an in-app popover, not the native picker: Chromium renders
   * that popup as browser chrome, so page CSS — `accent-color` included — cannot reach it
   * and it always draws in the browser's own blue, ignoring the tenant brand (#9).
   */
  import { CalendarDays } from "@lucide/svelte";

  import { addMonths, isoAddDays, monthGrid, monthOf } from "$lib/core/calendar";
  import { type DateFormat, getDateFormat } from "$lib/core/dateformat";
  import { fmtLongDay, fmtMonthYear, fmtWeekdayShort } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  let {
    name,
    value = $bindable(""),
    required = false,
    id = name,
    formId,
    onchange,
  }: {
    name: string;
    value?: string; // ISO yyyy-mm-dd
    required?: boolean;
    id?: string;
    /** Associate the posted value with an external <form id=…> (single-save layouts). */
    formId?: string;
    onchange?: (value: string) => void;
  } = $props();

  // The display order is the user's personal choice (issue #13), not a fixed European one. The
  // roles of the three typed fields follow from it; the parser and placeholder read the same table.
  const ORDER: Record<DateFormat, ("d" | "m" | "y")[]> = {
    "dd-mm-yyyy": ["d", "m", "y"],
    "yyyy-mm-dd": ["y", "m", "d"],
    "mm-dd-yyyy": ["m", "d", "y"],
  };
  const PLACEHOLDER: Record<DateFormat, string> = {
    "dd-mm-yyyy": "dd-mm-jjjj",
    "yyyy-mm-dd": "jjjj-mm-dd",
    "mm-dd-yyyy": "mm-dd-jjjj",
  };
  const dateFormat = $derived(getDateFormat());
  const placeholder = $derived(PLACEHOLDER[dateFormat]);

  function toDisplay(iso: string): string {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    if (!m) return "";
    const parts = { y: m[1], m: m[2], d: m[3] };
    return ORDER[dateFormat].map((role) => parts[role]).join("-");
  }

  function validDate(day: number, month: number, year: number): string | null {
    if (year < 100) year += 2000;
    const date = new Date(Date.UTC(year, month - 1, day));
    if (
      date.getUTCFullYear() !== year ||
      date.getUTCMonth() !== month - 1 ||
      date.getUTCDate() !== day
    ) {
      return null;
    }
    return date.toISOString().slice(0, 10);
  }

  // Assemble an ISO date from field values in whatever role order they were typed.
  function fromRoles(
    roles: ("d" | "m" | "y")[],
    values: number[],
    fallbackYear: number,
  ): string | null {
    const map: Partial<Record<"d" | "m" | "y", number>> = {};
    roles.forEach((role, i) => (map[role] = values[i]));
    return validDate(map.d ?? NaN, map.m ?? NaN, map.y ?? fallbackYear);
  }

  /**
   * Forgiving parser in the user's chosen order (issue #13): separators `-` `/` `.`, a two-field
   * short form (current year), and digit-only entry. ISO `yyyy-mm-dd` is always accepted too
   * because a four-digit lead is unambiguous whatever the preferred order.
   */
  function parseInput(raw: string): string | null {
    const text = raw.trim();
    if (!text) return null;
    const currentYear = new Date().getUTCFullYear();
    const order = ORDER[dateFormat];
    const nonYear = order.filter((r) => r !== "y"); // the two day/month roles, in order

    // ISO always wins: a 4-digit lead cannot be a day or month.
    const iso = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(text);
    if (iso) return validDate(Number(iso[3]), Number(iso[2]), Number(iso[1]));

    const three = /^(\d{1,4})[-/.](\d{1,4})[-/.](\d{1,4})$/.exec(text);
    if (three) return fromRoles(order, [Number(three[1]), Number(three[2]), Number(three[3])], currentYear);

    const two = /^(\d{1,2})[-/.](\d{1,2})$/.exec(text);
    if (two) return fromRoles(nonYear, [Number(two[1]), Number(two[2])], currentYear);

    // Digit-only. Year-first needs a 4-digit lead; the year-last orders take 2+2 then an
    // optional 2- or 4-digit year.
    if (/^\d+$/.test(text)) {
      if (order[0] === "y") {
        const full = /^(\d{4})(\d{2})(\d{2})$/.exec(text);
        if (full) return validDate(Number(full[3]), Number(full[2]), Number(full[1]));
        const md = /^(\d{2})(\d{2})$/.exec(text);
        if (md) return fromRoles(nonYear, [Number(md[1]), Number(md[2])], currentYear);
      } else {
        const digits = /^(\d{2})(\d{2})(\d{2}|\d{4})?$/.exec(text);
        if (digits) {
          const values = [Number(digits[1]), Number(digits[2])];
          if (digits[3]) return fromRoles(order, [...values, Number(digits[3])], currentYear);
          return fromRoles(nonYear, values, currentYear);
        }
      }
    }
    return null;
  }

  const todayIso = new Date().toISOString().slice(0, 10);

  let text = $state(toDisplay(value));
  let open = $state(false);
  // The month on screen and the arrow-key cursor, seeded from the value each time we open.
  let viewMonth = $state(monthOf(value || todayIso));
  let cursor = $state(value || todayIso);
  let rootEl: HTMLDivElement | undefined = $state();
  let gridEl: HTMLDivElement | undefined = $state();

  // Follow outside value changes (e.g. day navigation remounts or picker set).
  $effect(() => {
    text = toDisplay(value);
  });

  const days = $derived(monthGrid(viewMonth));
  const weekdays = $derived(days.slice(0, 7).map(fmtWeekdayShort));

  function commit(iso: string) {
    value = iso;
    text = toDisplay(iso);
    onchange?.(iso);
  }

  function openPopover() {
    cursor = value || todayIso;
    viewMonth = monthOf(cursor);
    open = true;
  }

  function closePopover(refocus = true) {
    open = false;
    if (refocus) document.getElementById(id)?.focus();
  }

  function pick(iso: string) {
    commit(iso);
    closePopover();
  }

  function onTextChange() {
    const iso = parseInput(text);
    if (iso) commit(iso);
    else if (!text.trim()) commit("");
    else text = toDisplay(value); // revert unreadable input
  }

  /** Move the cursor by n days, pulling the visible month along with it. */
  function moveCursor(n: number) {
    cursor = isoAddDays(cursor, n);
    viewMonth = monthOf(cursor);
  }

  function onGridKeydown(event: KeyboardEvent) {
    const moves: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
    };
    if (event.key in moves) {
      event.preventDefault();
      moveCursor(moves[event.key]);
    } else if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      const target = addMonths(viewMonth, event.key === "PageUp" ? -1 : 1);
      // Clamp to the target month, so 31 Jan + PageDown lands in February, not March.
      const lastDay = new Date(
        Date.UTC(Number(target.slice(0, 4)), Number(target.slice(5, 7)), 0),
      ).getUTCDate();
      cursor = `${target}-${String(Math.min(Number(cursor.slice(8, 10)), lastDay)).padStart(2, "0")}`;
      viewMonth = target;
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pick(cursor);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closePopover();
    }
  }

  // Keep DOM focus on the cursor cell so screen readers announce the day the arrows moved to.
  $effect(() => {
    if (!open) return;
    gridEl?.querySelector<HTMLButtonElement>(`[data-day="${cursor}"]`)?.focus();
  });

  $effect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootEl && !rootEl.contains(e.target as Node)) open = false;
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  });

  const dayClass = (day: string) => {
    const outside = monthOf(day) !== viewMonth;
    if (day === value) return "bg-brand font-medium text-white";
    if (day === todayIso) return "font-semibold text-brand ring-1 ring-brand ring-inset";
    return outside ? "text-text-muted hover:bg-surface" : "text-text hover:bg-surface";
  };
</script>

<div class="relative" bind:this={rootEl}>
  <input type="hidden" {name} {value} form={formId} />
  <input
    {id}
    type="text"
    inputmode="numeric"
    bind:value={text}
    {required}
    {placeholder}
    autocomplete="off"
    onchange={onTextChange}
    onkeydown={(e) => {
      if (e.key === "Enter") onTextChange();
      if (e.key === "Escape" && open) closePopover();
    }}
    class="w-full rounded-lg border border-border px-3 py-2 pr-9 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
  />
  <button
    type="button"
    tabindex="-1"
    class="absolute inset-y-0 right-2 flex items-center text-text-muted hover:text-brand"
    onclick={() => (open ? closePopover() : openPopover())}
    aria-label={t("calendar.title")}
    aria-expanded={open}
  >
    <CalendarDays size={16} />
  </button>

  {#if open}
    <!-- Anchored to the field (docs/UX.md). `right-0 sm:right-auto` keeps it on screen when
         the field sits near the right edge on a phone. -->
    <div
      class="absolute right-0 top-full z-50 mt-1 w-72 rounded-xl border border-border bg-surface-raised p-3 shadow-lg sm:right-auto sm:left-0"
    >
      <div class="mb-2 flex items-center justify-between">
        <button
          type="button"
          class="rounded p-1 text-text-muted hover:bg-surface hover:text-brand"
          onclick={() => (viewMonth = addMonths(viewMonth, -1))}
          aria-label={t("calendar.previous")}
        >
          ‹
        </button>
        <span class="text-sm font-medium capitalize text-text">{fmtMonthYear(viewMonth)}</span>
        <button
          type="button"
          class="rounded p-1 text-text-muted hover:bg-surface hover:text-brand"
          onclick={() => (viewMonth = addMonths(viewMonth, 1))}
          aria-label={t("calendar.next")}
        >
          ›
        </button>
      </div>

      <div class="grid grid-cols-7 gap-0.5 text-center">
        {#each weekdays as weekday (weekday)}
          <span class="py-1 text-[11px] font-medium uppercase text-text-muted">{weekday}</span>
        {/each}
      </div>

      <div bind:this={gridEl} class="grid grid-cols-7 gap-0.5 text-center">
        {#each days as day (day)}
          <button
            type="button"
            data-day={day}
            tabindex={day === cursor ? 0 : -1}
            aria-label={fmtLongDay(day)}
            aria-current={day === todayIso ? "date" : undefined}
            class="rounded py-1.5 text-sm {dayClass(day)}"
            onclick={() => pick(day)}
            onkeydown={onGridKeydown}
          >
            {Number(day.slice(8, 10))}
          </button>
        {/each}
      </div>

      <div class="mt-2 flex items-center justify-between border-t border-border pt-2">
        <button
          type="button"
          class="text-xs text-text-muted hover:text-brand"
          onclick={() => {
            commit("");
            closePopover();
          }}
        >
          {t("common.clear")}
        </button>
        <button
          type="button"
          class="text-xs text-brand hover:underline"
          onclick={() => pick(todayIso)}
        >
          {t("calendar.today")}
        </button>
      </div>
    </div>
  {/if}
</div>

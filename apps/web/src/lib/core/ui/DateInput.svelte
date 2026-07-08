<script lang="ts">
  /**
   * European date field. Browsers format native <input type="date"> after the *browser*
   * locale (often US), so this renders its own dd-mm-yyyy text input and posts the ISO
   * value through a hidden input. The calendar button still opens the native picker.
   * Typing accepts 7-7-2026, 07/07/2026 and 07.07.26.
   */
  import { CalendarDays } from "@lucide/svelte";

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

  function toDisplay(iso: string): string {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    return m ? `${m[3]}-${m[2]}-${m[1]}` : "";
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

  /**
   * Forgiving day-first parser: "15-07-2026", "15/7/26", "15.07", "15-7" (current year),
   * and digit-only "1507", "150726", "15072026".
   */
  function parseEuropean(raw: string): string | null {
    const text = raw.trim();
    if (!text) return null;
    const currentYear = new Date().getUTCFullYear();

    const full = /^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$/.exec(text);
    if (full) return validDate(Number(full[1]), Number(full[2]), Number(full[3]));

    const dayMonth = /^(\d{1,2})[-/.](\d{1,2})$/.exec(text);
    if (dayMonth) return validDate(Number(dayMonth[1]), Number(dayMonth[2]), currentYear);

    const digits = /^(\d{2})(\d{2})(\d{2}|\d{4})?$/.exec(text);
    if (digits) {
      return validDate(
        Number(digits[1]),
        Number(digits[2]),
        digits[3] ? Number(digits[3]) : currentYear,
      );
    }
    return null;
  }

  let text = $state(toDisplay(value));
  let pickerEl: HTMLInputElement | undefined = $state();

  // Follow outside value changes (e.g. day navigation remounts or picker set).
  $effect(() => {
    text = toDisplay(value);
  });

  function commit(iso: string) {
    value = iso;
    text = toDisplay(iso);
    onchange?.(iso);
  }

  function onTextChange() {
    const iso = parseEuropean(text);
    if (iso) commit(iso);
    else text = toDisplay(value); // revert unreadable input
  }

  function openPicker() {
    if (!pickerEl) return;
    pickerEl.value = value;
    try {
      pickerEl.showPicker();
    } catch {
      pickerEl.click();
    }
  }
</script>

<div class="relative">
  <input type="hidden" {name} {value} form={formId} />
  <input
    {id}
    type="text"
    inputmode="numeric"
    bind:value={text}
    {required}
    placeholder="dd-mm-jjjj"
    autocomplete="off"
    onchange={onTextChange}
    onkeydown={(e) => e.key === "Enter" && onTextChange()}
    class="w-full rounded-lg border border-neutral-300 px-3 py-2 pr-9 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
  />
  <button
    type="button"
    tabindex="-1"
    class="absolute inset-y-0 right-2 flex items-center text-neutral-400 hover:text-brand"
    onclick={openPicker}
    aria-label={name}
  >
    <CalendarDays size={16} />
  </button>
  <!-- Invisible native input overlaying the field: browsers anchor the calendar popup to
       the input's bounding box, so it must cover the visible field (a zero-size element
       makes the picker open at the window's top-left corner). -->
  <input
    bind:this={pickerEl}
    type="date"
    tabindex="-1"
    aria-hidden="true"
    class="pointer-events-none absolute inset-0 h-full w-full opacity-0"
    onchange={(e) => e.currentTarget.value && commit(e.currentTarget.value)}
  />
</div>

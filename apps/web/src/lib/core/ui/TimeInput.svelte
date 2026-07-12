<script lang="ts">
  /**
   * Time field. Native <input type="time"> renders after the *browser/OS* locale,
   * not <html lang>, so a user on an en-US machine gets an AM/PM picker no CSS can undo.
   * This owns the control instead: a text input on the "HH:MM" wire format, plus an anchored
   * quarter-hour dropdown. Typing accepts 9, 930, 9:30, 9.30, 9,30, 9u30 — and, whatever the
   * display preference, an explicit meridiem ("9:30 pm", "9pm", "930p").
   * Display follows the personal 12/24-hour clock (#13); the posted value stays 24-hour.
   */
  import { Clock } from "@lucide/svelte";
  import { getClock } from "$lib/core/dateformat";
  import { fmtClockTime } from "$lib/core/format";

  let {
    name,
    value = $bindable(""),
    required = false,
    id = name,
    formId,
    onchange,
  }: {
    name: string;
    value?: string; // 24-hour "HH:MM"
    required?: boolean;
    id?: string;
    /** Associate the posted value with an external <form id=…> (single-save layouts). */
    formId?: string;
    onchange?: (value: string) => void;
  } = $props();

  const STEP_MINUTES = 15;

  // Read once per component: the preference only changes on the Settings page, which navigates.
  const clock = getClock();

  function pad(n: number): string {
    return String(n).padStart(2, "0");
  }

  function toDisplay(time: string): string {
    const m = /^(\d{1,2}):(\d{2})$/.exec(time);
    if (!m) return "";
    const h = Number(m[1]);
    const min = Number(m[2]);
    return h <= 23 && min <= 59 ? fmtClockTime(`${pad(h)}:${pad(min)}`) : "";
  }

  /**
   * Forgiving parser → 24-hour "HH:MM", or null when unreadable.
   * Accepts "9", "9:30", "9.30", "9,30", "9u30", "9h30", "930", "0930", "14:05" — and an
   * explicit meridiem: "9:30 pm", "9pm", "930p", "12 am". Bare digits keep their 24-hour
   * meaning in both display modes, so a typed "14:05" never turns into 2:05 AM.
   */
  function parseTime(raw: string): string | null {
    let text = raw.trim().toLowerCase();
    if (!text) return null;

    let pm: boolean | null = null;
    const meridiem = /^(.+?)\s*([ap])\.?\s*(?:m\.?)?$/.exec(text);
    if (meridiem) {
      pm = meridiem[2] === "p";
      text = meridiem[1].trim();
    }

    let hours: number | null = null;
    let minutes = 0;
    const separated = /^(\d{1,2})\s*[:.,uh]\s*(\d{1,2})$/.exec(text);
    const digits = /^(\d{1,4})$/.exec(text);
    if (separated) {
      hours = Number(separated[1]);
      minutes = Number(separated[2]);
    } else if (digits) {
      const d = digits[1];
      hours = d.length <= 2 ? Number(d) : Number(d.slice(0, d.length - 2));
      minutes = d.length <= 2 ? 0 : Number(d.slice(-2));
    }
    if (hours === null) return null;

    if (pm !== null) {
      if (hours < 1 || hours > 12) return null;
      hours = (hours % 12) + (pm ? 12 : 0);
    }
    return validTime(hours, minutes);
  }

  function validTime(hours: number, minutes: number): string | null {
    if (hours > 23 || minutes > 59) return null;
    return `${pad(hours)}:${pad(minutes)}`;
  }

  const steps = Array.from({ length: (24 * 60) / STEP_MINUTES }, (_, i) => {
    const total = i * STEP_MINUTES;
    return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
  });

  /** Index in `steps` nearest the current time, rounded to the nearest STEP_MINUTES. */
  function nearestStepIndex(): number {
    const now = new Date();
    const totalMinutes = now.getHours() * 60 + now.getMinutes();
    return Math.round(totalMinutes / STEP_MINUTES) % steps.length;
  }

  // Writable derived: follows outside value changes (e.g. a duration edit back-computing the
  // end time), and holds the half-typed draft until it parses.
  let text = $derived(toDisplay(value));
  let open = $state(false);
  let highlighted = $state(0);
  let listEl: HTMLUListElement | undefined = $state();

  /**
   * Options matching what has been typed so far, compared on bare digits: "9" → 09:xx,
   * "14" → 14:xx, "930" → 09:30. In 12-hour display the typed digits also match the option's
   * displayed form ("9" → 9:00 AM *and* 9:00 PM). Never narrows to nothing — an unreadable
   * draft shows all.
   */
  const filtered = $derived.by(() => {
    const digits = text.replace(/\D/g, "");
    if (!digits) return steps;
    const prefix =
      digits.length <= 2 ? digits.padStart(2, "0") : digits.slice(0, 4).padStart(4, "0");
    const matches = steps.filter(
      (s) =>
        s.replace(":", "").startsWith(prefix) ||
        (clock === "12h" && toDisplay(s).replace(/\D/g, "").startsWith(digits)),
    );
    return matches.length ? matches : steps;
  });

  // Keep the highlighted option in view while typing or arrowing.
  $effect(() => {
    if (!open) return;
    const option = listEl?.children[highlighted];
    option?.scrollIntoView({ block: "nearest" });
  });

  function commit(time: string) {
    value = time;
    text = toDisplay(time);
    onchange?.(time);
  }

  function choose(time: string) {
    open = false;
    commit(time);
  }

  function onTextChange() {
    const time = parseTime(text);
    if (time) commit(time);
    else if (!text.trim() && !required) commit("");
    else text = toDisplay(value); // revert unreadable input
  }

  function onkeydown(e: KeyboardEvent) {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        open = true;
        e.preventDefault();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      highlighted = Math.min(highlighted + 1, filtered.length - 1);
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      highlighted = Math.max(highlighted - 1, 0);
      e.preventDefault();
    } else if (e.key === "Enter") {
      e.preventDefault();
      // A readable draft wins over the highlight, so "9:05" never snaps to a quarter hour.
      const typed = parseTime(text);
      if (typed) choose(typed);
      else if (filtered[highlighted]) choose(filtered[highlighted]);
    } else if (e.key === "Escape") {
      open = false;
      text = toDisplay(value);
    } else if (e.key === "Tab") {
      open = false;
    }
  }
</script>

<div class="relative">
  <input type="hidden" {name} {value} form={formId} />
  <input
    {id}
    type="text"
    inputmode="numeric"
    autocomplete="off"
    role="combobox"
    aria-expanded={open}
    aria-controls="{id}-listbox"
    bind:value={text}
    {required}
    placeholder={clock === "12h" ? "h:mm am" : "hh:mm"}
    class="w-full rounded-lg border border-border px-3 py-2 pr-9 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
    onfocus={() => {
      open = true;
      // A blank field (new entry) scrolls to now instead of 00:00; a filled one narrows
      // `filtered` to just its own value, so highlighting index 0 still lands on it.
      highlighted = text.trim() ? 0 : nearestStepIndex();
    }}
    oninput={() => {
      open = true;
      highlighted = 0;
    }}
    onchange={onTextChange}
    {onkeydown}
    onblur={() => {
      // Delay so an option mousedown can run first.
      setTimeout(() => (open = false), 120);
    }}
  />
  <span class="pointer-events-none absolute inset-y-0 right-2 flex items-center text-text-muted">
    <Clock size={16} />
  </span>

  {#if open}
    <ul
      bind:this={listEl}
      id="{id}-listbox"
      role="listbox"
      class="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
    >
      {#each filtered as step (step)}
        <li>
          <button
            type="button"
            role="option"
            aria-selected={step === value}
            class="w-full px-3 py-2 text-left text-sm tabular-nums hover:bg-surface
              {step === filtered[highlighted] ? 'bg-surface' : ''}
              {step === value ? 'font-medium text-brand' : 'text-text'}"
            onmousedown={(e) => {
              e.preventDefault();
              choose(step);
            }}
          >
            {toDisplay(step)}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

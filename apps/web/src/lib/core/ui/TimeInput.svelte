<script lang="ts">
  /**
   * 24-hour time field. Native <input type="time"> renders after the *browser/OS* locale,
   * not <html lang>, so a user on an en-US machine gets an AM/PM picker no CSS can undo.
   * This owns the control instead: a text input on the "HH:MM" wire format, plus an anchored
   * quarter-hour dropdown. Typing accepts 9, 930, 9:30, 9.30, 9,30 and 9u30.
   * (The 12/24-hour display preference of #13 belongs in `toDisplay`/`parseTime`.)
   */
  import { Clock } from "@lucide/svelte";

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

  function pad(n: number): string {
    return String(n).padStart(2, "0");
  }

  function toDisplay(time: string): string {
    const m = /^(\d{1,2}):(\d{2})$/.exec(time);
    if (!m) return "";
    const h = Number(m[1]);
    const min = Number(m[2]);
    return h <= 23 && min <= 59 ? `${pad(h)}:${pad(min)}` : "";
  }

  /**
   * Forgiving 24-hour parser → "HH:MM", or null when unreadable.
   * Accepts "9", "9:30", "9.30", "9,30", "9u30", "9h30", "930", "0930" and "14:05".
   */
  function parseTime(raw: string): string | null {
    const text = raw.trim().toLowerCase();
    if (!text) return null;

    const separated = /^(\d{1,2})\s*[:.,uh]\s*(\d{1,2})$/.exec(text);
    if (separated) return validTime(Number(separated[1]), Number(separated[2]));

    const digits = /^(\d{1,4})$/.exec(text);
    if (digits) {
      const d = digits[1];
      if (d.length <= 2) return validTime(Number(d), 0);
      return validTime(Number(d.slice(0, d.length - 2)), Number(d.slice(-2)));
    }
    return null;
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
   * "14" → 14:xx, "930" → 09:30. Never narrows to nothing — an unreadable draft shows all.
   */
  const filtered = $derived.by(() => {
    const digits = text.replace(/\D/g, "");
    if (!digits) return steps;
    const prefix =
      digits.length <= 2 ? digits.padStart(2, "0") : digits.slice(0, 4).padStart(4, "0");
    const matches = steps.filter((s) => s.replace(":", "").startsWith(prefix));
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
    placeholder="hh:mm"
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
            {step}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

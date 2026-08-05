<script lang="ts">
  /**
   * "Change these fields on the rows I picked" — one dialog for every list.
   *
   * The whole design is one rule: **a field you did not touch is not sent**. The dialog opens
   * blank over a selection that disagrees with itself — twelve domains at four registrars — so
   * an empty control can only honestly mean "leave each row's own alone". Reading it as "empty
   * them all" would wipe, on every row the user never looked at, exactly the value they had not
   * thought about. That is why clearing is a **separate, deliberate tick** rather than the same
   * blank control, and why only fields whose column says clearing is possible offer one at all.
   *
   * The payload therefore travels as JSON in one hidden field rather than as named inputs: the
   * difference between "absent" and "empty" is the entire contract, and an empty text input
   * posts `""` either way.
   *
   * It also decides which controls can appear here at all. Every one of them needs an
   * "unchanged" state, which is why there is no party picker (it always holds a type) and no
   * bare checkbox for a boolean (it is always either ticked or not) — a yes/no field is a
   * two-option type-ahead like any other closed vocabulary (#256).
   */
  import { untrack } from "svelte";

  import { enhance } from "$app/forms";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import type { BulkFieldDef } from "./types";

  let {
    open = $bindable(false),
    fields,
    selected,
    action = "?/bulkUpdate",
    fieldErrors = null,
  }: {
    open?: boolean;
    fields: BulkFieldDef[];
    /** The ids the batch applies to. */
    selected: string[];
    action?: string;
    /** Per-field message keys from a rejected save (the API's 422 `fields`). */
    fieldErrors?: Record<string, string> | null;
  } = $props();

  const busy = new InFlight();

  /** What the user typed or picked, per field key. Empty string = untouched. */
  let entered = $state<Record<string, string>>({});
  /** Fields explicitly marked for clearing — the deliberate half of the rule above. */
  let cleared = $state<Record<string, boolean>>({});

  /**
   * Opening is starting over: a value left behind from the last selection would otherwise be
   * applied to this one on the first press of Save.
   *
   * `untrack` so only `open` re-runs it: `fields` is `$derived` on most lists (its picker
   * options come from the page's own lookups), and a lookup refreshing under an open dialog
   * would otherwise wipe what the user had just filled in.
   */
  $effect(() => {
    if (!open) return;
    untrack(() => {
      entered = {};
      cleared = {};
    });
  });

  /**
   * A **function binding** rather than `bind:value={entered[key]}`, because the record starts
   * empty: `bind:` against a missing key hands the control `undefined`, and a control whose own
   * prop has a fallback treats that as an error — the dialog threw and rendered nothing at all.
   * Seeding every key up front would work until the day a field arrives late; a getter that
   * answers `""` cannot have that day.
   */
  const bindTo = (key: string) =>
    [() => entered[key] ?? "", (value: string) => (entered[key] = value)] as const;

  const boolOptions = $derived([
    { value: "true", label: t("common.yes") },
    { value: "false", label: t("common.no") },
  ]);

  /**
   * "Empty it" and "set it to this" are the same decision asked two ways, so the two controls
   * cannot both hold an answer: ticking the box drops whatever was picked, and picking
   * something unticks the box.
   *
   * Done in the gestures rather than by disabling the control, because neither `Combobox` nor
   * `DateInput` takes a `disabled` prop — and those are exactly the controls the clearable
   * fields use, so a `disabled` attribute would have looked right on the one field that did not
   * need it and done nothing on the three that did.
   */
  function setCleared(key: string, on: boolean) {
    cleared[key] = on;
    if (on) entered[key] = ""; // flows back through the control's own binding
  }
  /** The control was given an answer, so "empty it" is no longer the one on record. */
  function answered(key: string, value: string) {
    if (value !== "") cleared[key] = false;
  }

  /** `{key: value}` for every field the user actually decided about — nothing else. */
  const payload = $derived.by(() => {
    const out: Record<string, string | null> = {};
    for (const field of fields) {
      if (cleared[field.key]) out[field.key] = null;
      else if ((entered[field.key] ?? "") !== "") out[field.key] = entered[field.key];
    }
    return out;
  });

  const changes = $derived(Object.keys(payload).length);
</script>

<Modal bind:open title={t("bulk.edit_title")}>
  <form
    method="POST"
    {action}
    class="space-y-4"
    use:enhance={busy.wrap("bulk-update", () => async ({ update }) => {
      open = false;
      // `reset: true`: this form starts something new every time it opens — a different
      // selection, decided from scratch — which is what the `$effect` above already enforces.
      // Stating it is what `pnpm forms:check` asks for (docs/UX.md).
      await update({ reset: true });
    })}
  >
    <input type="hidden" name="ids" value={selected.join(",")} />
    <input type="hidden" name="values" value={JSON.stringify(payload)} />

    <p class="text-sm text-text-muted">{t("bulk.edit_hint", { count: selected.length })}</p>

    <div class="space-y-3">
      {#each fields as field (field.key)}
        {@const [read, write] = bindTo(field.key)}
        <div>
          <span class="mb-1 block text-sm text-text" id="bulk-label-{field.key}">
            {field.label}
          </span>
          <div class="flex flex-wrap items-center gap-2">
            <div class="min-w-0 flex-1">
              <!-- `bind:value` still owns the value; the callback only ever unticks "empty it",
                   so the two controls can never both hold an answer. -->
              {#if field.type === "select" || field.type === "fk" || field.type === "bool"}
                <Combobox
                  items={field.type === "bool" ? boolOptions : (field.options ?? [])}
                  name="_bulk_{field.key}"
                  bind:value={read, write}
                  placeholder={t("bulk.unchanged")}
                  ariaLabel={field.label}
                  id="bulk-{field.key}"
                  onselect={(value) => answered(field.key, value)}
                />
              {:else if field.type === "date"}
                <DateInput
                  name="_bulk_{field.key}"
                  bind:value={read, write}
                  onchange={(value) => answered(field.key, value)}
                />
              {:else}
                <input
                  type={field.type === "number" ? "number" : "text"}
                  class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
                  placeholder={field.placeholder ?? t("bulk.unchanged")}
                  aria-labelledby="bulk-label-{field.key}"
                  value={read()}
                  oninput={(e) => {
                    write(e.currentTarget.value);
                    answered(field.key, e.currentTarget.value);
                  }}
                />
              {/if}
            </div>
            {#if field.clearable}
              <!-- Clearing is its own tick, never a blank control (see the component note). -->
              <label class="flex shrink-0 items-center gap-1.5 text-xs text-text-muted">
                <input
                  type="checkbox"
                  class="h-3.5 w-3.5 rounded border-border text-brand focus:ring-brand"
                  checked={cleared[field.key] === true}
                  onchange={(e) => setCleared(field.key, e.currentTarget.checked)}
                />
                {field.clearLabel ?? t("bulk.clear_field")}
              </label>
            {/if}
          </div>
          {#if fieldErrors?.[field.key]}
            <p class="mt-1 text-xs text-red-600 dark:text-red-400">
              {t(fieldErrors[field.key])}
            </p>
          {/if}
        </div>
      {/each}
    </div>

    <div class="flex items-center justify-end gap-2 pt-1">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface"
        onclick={() => (open = false)}
      >
        {t("common.cancel")}
      </button>
      <!-- Nothing decided is not a save: the button says so, rather than reporting a batch
           that changed nothing. -->
      <Button
        type="submit"
        loading={busy.is("bulk-update")}
        disabled={changes === 0 || busy.active}
      >
        {t("bulk.apply", { count: selected.length })}
      </Button>
    </div>
  </form>
</Modal>

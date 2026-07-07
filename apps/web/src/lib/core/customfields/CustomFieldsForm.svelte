<script lang="ts">
  /**
   * Generic custom-fields form (CLAUDE.md §13). Renders one input per tenant definition and
   * serialises all values into a single hidden `custom` field (JSON) so a SvelteKit form action
   * can forward it to the API as one object. Every module inherits this — no per-module code.
   */
  import { t } from "$lib/core/i18n";
  import type { CustomFieldDefinition } from "./types";
  import { fieldLabel, optionLabel } from "./types";

  let {
    definitions,
    values = {},
    locale,
  }: {
    definitions: CustomFieldDefinition[];
    values?: Record<string, unknown>;
    locale: string;
  } = $props();

  // Reactive working copy; serialised to the hidden input below. Built once from the props
  // (the form is remounted when its definitions/values change), so a closure is correct here.
  function buildInitial(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const def of definitions) {
      out[def.key] =
        values[def.key] ??
        (def.data_type === "multi_select" ? [] : def.data_type === "boolean" ? false : "");
    }
    return out;
  }
  let state = $state(buildInitial());
  const json = $derived(JSON.stringify(state));

  function setValue(key: string, value: unknown) {
    state[key] = value;
  }

  function toggleMulti(key: string, value: string, checked: boolean) {
    const current = new Set((state[key] as string[]) ?? []);
    if (checked) current.add(value);
    else current.delete(value);
    state[key] = [...current];
  }

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<input type="hidden" name="custom" value={json} />

{#if definitions.length > 0}
  <div class="grid gap-3 sm:grid-cols-2">
    {#each definitions as def (def.key)}
      <div class:sm:col-span-2={def.data_type === "long_text" || def.data_type === "multi_select"}>
        <label for={`cf-${def.key}`} class="mb-1 block text-sm font-medium text-neutral-700">
          {fieldLabel(def, locale)}
          {#if def.required}<span class="text-red-500">*</span>{/if}
        </label>

        {#if def.data_type === "long_text"}
          <textarea
            id={`cf-${def.key}`}
            rows="3"
            class={inputClass}
            value={String(state[def.key] ?? "")}
            oninput={(e) => setValue(def.key, e.currentTarget.value)}
          ></textarea>
        {:else if def.data_type === "boolean"}
          <input
            id={`cf-${def.key}`}
            type="checkbox"
            class="h-4 w-4 rounded border-neutral-300"
            checked={Boolean(state[def.key])}
            onchange={(e) => setValue(def.key, e.currentTarget.checked)}
          />
        {:else if def.data_type === "select"}
          <select
            id={`cf-${def.key}`}
            class={inputClass}
            value={String(state[def.key] ?? "")}
            onchange={(e) => setValue(def.key, e.currentTarget.value)}
          >
            <option value="">{t("common.none")}</option>
            {#each def.options_json ?? [] as opt (opt.value)}
              <option value={opt.value}>{optionLabel(opt, locale)}</option>
            {/each}
          </select>
        {:else if def.data_type === "multi_select"}
          <div class="flex flex-wrap gap-3">
            {#each def.options_json ?? [] as opt (opt.value)}
              <label class="flex items-center gap-1.5 text-sm text-neutral-700">
                <input
                  type="checkbox"
                  class="h-4 w-4 rounded border-neutral-300"
                  checked={((state[def.key] as string[]) ?? []).includes(opt.value)}
                  onchange={(e) => toggleMulti(def.key, opt.value, e.currentTarget.checked)}
                />
                {optionLabel(opt, locale)}
              </label>
            {/each}
          </div>
        {:else}
          <input
            id={`cf-${def.key}`}
            type={def.data_type === "number"
              ? "number"
              : def.data_type === "date"
                ? "date"
                : def.data_type === "datetime"
                  ? "datetime-local"
                  : def.data_type === "email"
                    ? "email"
                    : def.data_type === "url"
                      ? "url"
                      : def.data_type === "phone"
                        ? "tel"
                        : "text"}
            class={inputClass}
            value={String(state[def.key] ?? "")}
            oninput={(e) => setValue(def.key, e.currentTarget.value)}
          />
        {/if}
      </div>
    {/each}
  </div>
{/if}

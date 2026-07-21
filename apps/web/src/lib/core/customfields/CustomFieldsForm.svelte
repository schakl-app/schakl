<script lang="ts">
  /**
   * Generic custom-fields form (CLAUDE.md §13). Renders one input per tenant definition and
   * serialises all values into a single hidden `custom` field (JSON) so a SvelteKit form action
   * can forward it to the API as one object. Every module inherits this — no per-module code.
   *
   * Outside a form (a draft held in client state), pass `name={null}` and read the values back
   * through `onchange`.
   */
  import { t } from "$lib/core/i18n";
  import type { CandidateScope } from "$lib/core/richtext/candidates";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import type { CustomFieldDefinition } from "./types";
  import { fieldLabel, optionLabel } from "./types";

  let {
    definitions,
    values = {},
    locale,
    name = "custom",
    scope,
    onchange,
  }: {
    definitions: CustomFieldDefinition[];
    values?: Record<string, unknown>;
    locale: string;
    /** Name of the hidden input; `null` renders none, for use outside a form (report via `onchange`). */
    name?: string | null;
    /** Host context for the long-text editors' @/# candidates (#237). */
    scope?: CandidateScope;
    onchange?: (values: Record<string, unknown>) => void;
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
  let fieldValues = $state(buildInitial());
  const json = $derived(JSON.stringify(fieldValues));

  function setValue(key: string, value: unknown) {
    fieldValues[key] = value;
    onchange?.($state.snapshot(fieldValues));
  }

  function toggleMulti(key: string, value: string, checked: boolean) {
    const current = new Set((fieldValues[key] as string[]) ?? []);
    if (checked) current.add(value);
    else current.delete(value);
    setValue(key, [...current]);
  }

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

{#if name}
  <input type="hidden" {name} value={json} />
{/if}

{#if definitions.length > 0}
  <div class="grid gap-3 sm:grid-cols-2">
    {#each definitions as def (def.key)}
      <div class:sm:col-span-2={def.data_type === "long_text" || def.data_type === "multi_select"}>
        <label for={`cf-${def.key}`} class="mb-1 block text-sm font-medium text-neutral-700">
          {fieldLabel(def, locale)}
          {#if def.required}<span class="text-red-500">*</span>{/if}
        </label>

        {#if def.data_type === "long_text"}
          <!-- LONG_TEXT is markdown, authored through the shared editor (issue #66). `name={null}`:
               this form serialises every value into one hidden `custom` field, so the editor reports
               changes via `onchange` rather than submitting its own field. -->
          <RichTextEditor
            id={`cf-${def.key}`}
            name={null}
            rows={3}
            value={String(fieldValues[def.key] ?? "")}
            {scope}
            onchange={(v) => setValue(def.key, v)}
          />
        {:else if def.data_type === "boolean"}
          <input
            id={`cf-${def.key}`}
            type="checkbox"
            class="h-4 w-4 rounded border-neutral-300"
            checked={Boolean(fieldValues[def.key])}
            onchange={(e) => setValue(def.key, e.currentTarget.checked)}
          />
        {:else if def.data_type === "select"}
          <select
            id={`cf-${def.key}`}
            class={inputClass}
            value={String(fieldValues[def.key] ?? "")}
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
                  checked={((fieldValues[def.key] as string[]) ?? []).includes(opt.value)}
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
            value={String(fieldValues[def.key] ?? "")}
            oninput={(e) => setValue(def.key, e.currentTarget.value)}
          />
        {/if}
      </div>
    {/each}
  </div>
{/if}

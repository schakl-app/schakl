<script lang="ts">
  /**
   * The manual contactmoment form (meeting / call / note), rendered inside a `Modal` by the
   * panel body. One save button; posts to the **host page's** `?/createInteraction` or
   * `?/updateInteraction` action (a panel edits through its host, docs/UX.md).
   *
   * The date+time post as the tenant's wall clock (naive); the API attaches the org zone, so
   * a hand-typed 14:00 lands on the same timeline instant the reader sees.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import { type InteractionItem, instantToLocal } from "./format";

  let {
    interaction = null,
    prefill = {},
    mentions = [],
    onsaved,
  }: {
    /** Existing row when editing; null for create. */
    interaction?: InteractionItem | null;
    /** The host entity's link, stamped on new rows (e.g. `{ company_id }`). */
    prefill?: Record<string, string | null | undefined>;
    /** Org members offered by the note editor's @ autocomplete (#151). */
    mentions?: { id: string; name: string }[];
    onsaved?: () => void;
  } = $props();

  const local = interaction ? instantToLocal(interaction.occurred_at) : null;
  let kind = $state(interaction?.kind ?? "meeting");
  let date = $state(local?.date ?? new Date().toISOString().slice(0, 10));
  let time = $state(local?.time ?? "");
  let error = $state("");

  const KINDS = ["meeting", "call", "note"] as const;
  const DIRECTIONS = ["none", "inbound", "outbound"] as const;

  const links = $derived(
    interaction
      ? {}
      : Object.fromEntries(
          Object.entries(prefill).filter(([, v]) => typeof v === "string" && v.length > 0),
        ),
  );
</script>

<form
  method="POST"
  action={interaction ? "?/updateInteraction" : "?/createInteraction"}
  class="space-y-4"
  use:enhance={() =>
    async ({ result, update }) => {
      if (result.type === "failure") {
        error = String(result.data?.error ?? "errors.validation");
        return;
      }
      error = "";
      await update({ reset: false });
      onsaved?.();
    }}
>
  {#if interaction}
    <input type="hidden" name="id" value={interaction.id} />
  {/if}
  {#each Object.entries(links) as [field, value] (field)}
    <input type="hidden" name={field} {value} />
  {/each}

  <div class="grid gap-4 sm:grid-cols-2">
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.kind")}</span>
      <select
        name="kind"
        bind:value={kind}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
      >
        {#each KINDS as option (option)}
          <option value={option}>{t(`interactions.kind.${option}`)}</option>
        {/each}
      </select>
    </label>
    <div class="grid grid-cols-2 gap-2">
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.date")}</span>
        <DateInput name="occurred_date" bind:value={date} required />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.time")}</span>
        <TimeInput name="occurred_time" bind:value={time} />
      </label>
    </div>
  </div>

  <label class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.subject")}</span>
    <input
      name="subject"
      value={interaction?.subject ?? ""}
      required
      maxlength="500"
      class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
    />
  </label>

  {#if kind === "call"}
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.direction")}</span>
      <select
        name="direction"
        value={interaction?.direction ?? "none"}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
      >
        {#each DIRECTIONS as option (option)}
          <option value={option}>{t(`interactions.direction.${option}`)}</option>
        {/each}
      </select>
    </label>
  {/if}

  <div class="text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.notes")}</span>
    <RichTextEditor name="body_text" value={interaction?.body_text ?? ""} rows={4} {mentions} />
  </div>

  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end">
    <button
      type="submit"
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("common.save")}
    </button>
  </div>
</form>

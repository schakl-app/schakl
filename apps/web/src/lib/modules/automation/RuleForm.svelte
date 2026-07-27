<script lang="ts" module>
  /** Shapes shared with the settings routes (derived from the API's generated types there). */
  export type CatalogTrigger = { event: string; entity_type: string };
  export type Member = { user_id: string; full_name: string | null; email: string };
  export type Template = { id: string; name: string };
  export type RuleActionValue = { action_type: string; config: Record<string, unknown> };
  export type RuleValue = {
    name: string;
    trigger_event: string;
    enabled: boolean;
    conditions: Record<string, unknown>;
    actions: RuleActionValue[];
  };
  export type DryRunOutcome = {
    matched: boolean;
    would_fire: string[];
    snapshot_found: boolean;
  };
</script>

<script lang="ts">
  import { X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import type { SubmitFunction } from "@sveltejs/kit";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  let {
    rule = null,
    triggers,
    actionTypes,
    members,
    templates,
    action,
    error = null,
    dryRun = null,
  }: {
    rule?: RuleValue | null;
    triggers: CatalogTrigger[];
    actionTypes: string[];
    members: Member[];
    templates: Template[];
    /** The save form action, e.g. "?/create" or "?/update". Dry-run posts to "?/dryRun". */
    action: string;
    error?: string | null;
    dryRun?: DryRunOutcome | null;
  } = $props();

  const OPS = ["eq", "ne", "in", "contains", "gt", "lt"] as const;
  const TASK_STATUSES = ["open", "in_progress", "done"] as const;

  type ConditionRow = { field: string; op: string; value: string };

  // --- conditions: parse the stored tree into flat rows where honestly possible ---------- //
  function isLeaf(node: unknown): node is { field: string; op: string; value: unknown } {
    return (
      typeof node === "object" &&
      node !== null &&
      "field" in node &&
      "op" in node &&
      "value" in node
    );
  }
  function valueText(value: unknown): string {
    if (Array.isArray(value)) return value.map(String).join(", ");
    return value == null ? "" : String(value);
  }
  function parseRows(tree: Record<string, unknown> | undefined): {
    mode: "all" | "any";
    rows: ConditionRow[];
    advanced: boolean;
  } {
    if (!tree || Object.keys(tree).length === 0) return { mode: "all", rows: [], advanced: false };
    if (isLeaf(tree)) {
      return {
        mode: "all",
        rows: [{ field: tree.field, op: tree.op, value: valueText(tree.value) }],
        advanced: false,
      };
    }
    for (const mode of ["all", "any"] as const) {
      const children = tree[mode];
      if (Array.isArray(children) && children.every(isLeaf)) {
        return {
          mode,
          rows: children.map((c) => ({ field: c.field, op: c.op, value: valueText(c.value) })),
          advanced: false,
        };
      }
    }
    return { mode: "all", rows: [], advanced: true }; // a nested tree only JSON can express
  }

  const parsed = parseRows(rule?.conditions);

  let name = $state(rule?.name ?? "");
  let triggerEvent = $state(rule?.trigger_event ?? triggers[0]?.event ?? "");
  let enabled = $state(rule?.enabled ?? true);
  let mode = $state<"all" | "any">(parsed.mode);
  let rows = $state<ConditionRow[]>(parsed.rows);
  let advanced = $state(parsed.advanced);
  let advancedText = $state(JSON.stringify(rule?.conditions ?? {}, null, 2));
  let actions = $state<RuleActionValue[]>(
    (rule?.actions ?? []).map((a) => ({ action_type: a.action_type, config: { ...a.config } })),
  );
  let dryRunEntity = $state("");

  const busy = new InFlight();
  // Save and dry-run share the form (#279): key off the clicked button's formaction.
  const submit: SubmitFunction = (input) =>
    busy.wrap(
      input.submitter?.getAttribute("formaction") === "?/dryRun" ? "dryRun" : "save",
      () =>
        ({ update }) => {
          // Keep the editor's values: a dry run (or a validation error) must not wipe the form.
          void update({ reset: false });
        },
    )(input);

  function coerce(value: string): unknown {
    const trimmed = value.trim();
    if (trimmed !== "" && !Number.isNaN(Number(trimmed))) return Number(trimmed);
    if (trimmed === "true") return true;
    if (trimmed === "false") return false;
    return trimmed;
  }
  function rowNode(row: ConditionRow): Record<string, unknown> {
    const value =
      row.op === "in" ? row.value.split(",").map((part) => coerce(part)) : coerce(row.value);
    return { field: row.field.trim(), op: row.op, value };
  }

  const conditionsJson = $derived.by(() => {
    if (advanced) return advancedText;
    const leaves = rows.filter((row) => row.field.trim() !== "").map(rowNode);
    if (leaves.length === 0) return "{}";
    if (leaves.length === 1) return JSON.stringify(leaves[0]);
    return JSON.stringify({ [mode]: leaves });
  });
  const advancedInvalid = $derived.by(() => {
    if (!advanced) return false;
    try {
      JSON.parse(advancedText);
      return false;
    } catch {
      return true;
    }
  });
  const actionsJson = $derived(JSON.stringify(actions));

  function addRow() {
    rows.push({ field: "", op: "eq", value: "" });
  }
  function addAction() {
    actions.push({ action_type: actionTypes[0] ?? "task.create", config: {} });
  }
  function setConfig(index: number, key: string, value: unknown) {
    actions[index].config = { ...actions[index].config, [key]: value };
  }
  function recipientIds(config: Record<string, unknown>): string[] {
    const raw = config.user_ids;
    return Array.isArray(raw) ? raw.map(String) : [];
  }
  function toggleRecipient(index: number, userId: string, checked: boolean) {
    const others = recipientIds(actions[index].config).filter((id) => id !== userId);
    setConfig(index, "user_ids", checked ? [...others, userId] : others);
  }
  function memberLabel(member: Member): string {
    return member.full_name || member.email;
  }

  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";
</script>

<form method="POST" {action} use:enhance={submit} class="space-y-6">
  <input type="hidden" name="conditions" value={conditionsJson} />
  <input type="hidden" name="actions" value={actionsJson} />

  <!-- Definition -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="rule-name" class={labelClass}>{t("automation.name")}</label>
        <input id="rule-name" name="name" bind:value={name} required class={inputClass} />
      </div>
      <div>
        <label for="rule-trigger" class={labelClass}>{t("automation.trigger")}</label>
        <select id="rule-trigger" name="trigger_event" bind:value={triggerEvent} class={inputClass}>
          {#each triggers as trigger (trigger.event)}
            <option value={trigger.event}>{t(`automation.trigger.${trigger.event}`)}</option>
          {/each}
        </select>
        <p class="mt-1 text-xs text-text-muted">{t("automation.trigger_hint")}</p>
      </div>
    </div>
    <label class="mt-3 flex items-center gap-2 text-sm text-text">
      <input
        type="checkbox"
        name="enabled"
        bind:checked={enabled}
        class="h-4 w-4 rounded border-border"
      />
      {t("automation.enabled")}
    </label>
  </section>

  <!-- Conditions -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("automation.conditions")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("automation.conditions_hint")}</p>

    {#if !advanced}
      {#if rows.length > 1}
        <select
          bind:value={mode}
          class="mt-3 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
        >
          <option value="all">{t("automation.conditions_mode_all")}</option>
          <option value="any">{t("automation.conditions_mode_any")}</option>
        </select>
      {/if}
      <div class="mt-3 space-y-2">
        {#each rows as row, index (index)}
          <div class="flex flex-wrap items-center gap-2">
            <input
              bind:value={row.field}
              placeholder={t("automation.condition_field")}
              aria-label={t("automation.condition_field")}
              class="{inputClass} min-w-0 flex-1 sm:max-w-48"
            />
            <select
              bind:value={row.op}
              aria-label={t("automation.trigger")}
              class="rounded-lg border border-border bg-surface px-2 py-2 text-sm"
            >
              {#each OPS as op (op)}<option value={op}>{t(`automation.op.${op}`)}</option>{/each}
            </select>
            <input
              bind:value={row.value}
              placeholder={t("automation.condition_value")}
              aria-label={t("automation.condition_value")}
              class="{inputClass} min-w-0 flex-1 sm:max-w-48"
            />
            <button
              type="button"
              aria-label={t("automation.condition_remove")}
              class="rounded p-1 text-text-muted hover:text-red-600"
              onclick={() => rows.splice(index, 1)}
            >
              <X size={16} />
            </button>
          </div>
        {/each}
      </div>
      <div class="mt-2 flex items-center gap-4">
        <button type="button" class="text-sm text-brand hover:underline" onclick={addRow}>
          {t("automation.condition_add")}
        </button>
        <span class="text-xs text-text-muted">{t("automation.condition_value_hint")}</span>
      </div>
    {/if}

    <label class="mt-4 flex items-center gap-2 text-sm text-text">
      <input
        type="checkbox"
        checked={advanced}
        class="h-4 w-4 rounded border-border"
        onchange={(event) => {
          const on = event.currentTarget.checked;
          if (on)
            advancedText =
              conditionsJson === "{}" ? "{}" : JSON.stringify(JSON.parse(conditionsJson), null, 2);
          advanced = on;
        }}
      />
      {t("automation.advanced_json")}
    </label>
    {#if advanced}
      <textarea
        bind:value={advancedText}
        rows="6"
        aria-label={t("automation.advanced_json")}
        class="{inputClass} mt-2 font-mono text-xs"></textarea>
      <p class="mt-1 text-xs text-text-muted">{t("automation.advanced_json_hint")}</p>
      {#if advancedInvalid}
        <p class="mt-1 text-sm text-red-600">{t("automation.advanced_json_invalid")}</p>
      {/if}
    {/if}
  </section>

  <!-- Actions -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("automation.actions")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("automation.actions_hint")}</p>

    {#if actions.length === 0}
      <p class="mt-3 text-sm text-text-muted">{t("automation.actions_empty")}</p>
    {/if}
    <div class="mt-3 space-y-3">
      {#each actions as entry, index (index)}
        <div class="rounded-lg border border-border p-3">
          <div class="flex items-center gap-2">
            <select
              value={entry.action_type}
              aria-label={t("automation.actions")}
              class="rounded-lg border border-border bg-surface px-2 py-2 text-sm"
              onchange={(event) => {
                actions[index] = { action_type: event.currentTarget.value, config: {} };
              }}
            >
              {#each actionTypes as type (type)}
                <option value={type}>{t(`automation.action.${type}`)}</option>
              {/each}
            </select>
            <div class="flex-1"></div>
            <button
              type="button"
              aria-label={t("automation.action_remove")}
              class="rounded p-1 text-text-muted hover:text-red-600"
              onclick={() => actions.splice(index, 1)}
            >
              <X size={16} />
            </button>
          </div>

          <div class="mt-3 grid gap-3 sm:grid-cols-2">
            {#if entry.action_type === "task.create"}
              <div>
                <span class={labelClass}>{t("automation.config.template")}</span>
                <select
                  value={String(entry.config.template_id ?? "")}
                  class={inputClass}
                  onchange={(event) => setConfig(index, "template_id", event.currentTarget.value)}
                >
                  <option value="">{t("automation.config.template_none")}</option>
                  {#each templates as template (template.id)}
                    <option value={template.id}>{template.name}</option>
                  {/each}
                </select>
              </div>
              {#if !entry.config.template_id}
                <div>
                  <span class={labelClass}>{t("automation.config.title")}</span>
                  <input
                    value={String(entry.config.title ?? "")}
                    class={inputClass}
                    oninput={(event) => setConfig(index, "title", event.currentTarget.value)}
                  />
                </div>
                <div>
                  <span class={labelClass}>{t("automation.config.assignee")}</span>
                  <select
                    value={String(entry.config.assignee_user_id ?? "")}
                    class={inputClass}
                    onchange={(event) =>
                      setConfig(index, "assignee_user_id", event.currentTarget.value)}
                  >
                    <option value="">—</option>
                    {#each members as member (member.user_id)}
                      <option value={member.user_id}>{memberLabel(member)}</option>
                    {/each}
                  </select>
                </div>
              {/if}
              <p class="text-xs text-text-muted sm:col-span-2">
                {t("automation.config.company_hint")}
              </p>
            {:else if entry.action_type === "task.set_status"}
              <div>
                <span class={labelClass}>{t("automation.config.status")}</span>
                <select
                  value={String(entry.config.status ?? "open")}
                  class={inputClass}
                  onchange={(event) => setConfig(index, "status", event.currentTarget.value)}
                >
                  {#each TASK_STATUSES as status (status)}
                    <option value={status}>{t(`tasks.status.${status}`)}</option>
                  {/each}
                </select>
              </div>
            {:else if entry.action_type === "task.assign"}
              <div>
                <span class={labelClass}>{t("automation.config.assignee")}</span>
                <select
                  value={String(entry.config.user_id ?? "")}
                  class={inputClass}
                  onchange={(event) => setConfig(index, "user_id", event.currentTarget.value)}
                >
                  <option value="">—</option>
                  {#each members as member (member.user_id)}
                    <option value={member.user_id}>{memberLabel(member)}</option>
                  {/each}
                </select>
              </div>
            {:else if entry.action_type === "notification.send"}
              <div class="sm:col-span-2">
                <span class={labelClass}>{t("automation.config.message")}</span>
                <textarea
                  value={String(entry.config.message ?? "")}
                  rows="2"
                  class={inputClass}
                  oninput={(event) => setConfig(index, "message", event.currentTarget.value)}
                ></textarea>
              </div>
              <div class="sm:col-span-2">
                <span class={labelClass}>{t("automation.config.recipients")}</span>
                <div class="flex flex-wrap gap-x-4 gap-y-1">
                  {#each members as member (member.user_id)}
                    <label class="flex items-center gap-2 text-sm text-text">
                      <input
                        type="checkbox"
                        checked={recipientIds(entry.config).includes(member.user_id)}
                        class="h-4 w-4 rounded border-border"
                        onchange={(event) =>
                          toggleRecipient(index, member.user_id, event.currentTarget.checked)}
                      />
                      {memberLabel(member)}
                    </label>
                  {/each}
                </div>
              </div>
            {:else if entry.action_type === "webhook.post"}
              <div class="sm:col-span-2">
                <span class={labelClass}>{t("automation.config.url")}</span>
                <input
                  value={String(entry.config.url ?? "")}
                  placeholder="https://"
                  class={inputClass}
                  oninput={(event) => setConfig(index, "url", event.currentTarget.value)}
                />
              </div>
              <div class="sm:col-span-2">
                <label class="flex items-center gap-2 text-sm text-text">
                  <input
                    type="checkbox"
                    checked={Boolean(entry.config.confirm)}
                    class="h-4 w-4 rounded border-border"
                    onchange={(event) => setConfig(index, "confirm", event.currentTarget.checked)}
                  />
                  {t("automation.config.confirm")}
                </label>
                <p class="mt-1 text-xs text-text-muted">{t("automation.config.confirm_hint")}</p>
              </div>
            {/if}
          </div>
        </div>
      {/each}
    </div>
    <button type="button" class="mt-3 text-sm text-brand hover:underline" onclick={addAction}>
      {t("automation.action_add")}
    </button>
  </section>

  <!-- Dry run -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-sm font-semibold text-text">{t("automation.dry_run")}</h2>
    <p class="mt-1 text-xs text-text-muted">{t("automation.dry_run_hint")}</p>
    <div class="mt-3 flex flex-wrap items-center gap-2">
      <input
        name="entity_id"
        bind:value={dryRunEntity}
        placeholder={t("automation.dry_run_entity")}
        aria-label={t("automation.dry_run_entity")}
        class="{inputClass} min-w-0 flex-1 sm:max-w-96 font-mono text-xs"
      />
      <Button
        variant="secondary"
        formaction="?/dryRun"
        formnovalidate
        loading={busy.is("dryRun")}
        disabled={busy.active}
      >
        {t("automation.dry_run")}
      </Button>
    </div>
    {#if dryRun}
      <div class="mt-3 text-sm">
        {#if dryRun.matched && dryRun.would_fire.length > 0}
          <p class="text-text">
            {t("automation.dry_run_matched", {
              actions: dryRun.would_fire.map((key) => t(`automation.action.${key}`)).join(", "),
            })}
          </p>
        {:else if dryRun.matched}
          <p class="text-text">{t("automation.dry_run_matched_none")}</p>
        {:else}
          <p class="text-text-muted">{t("automation.dry_run_not_matched")}</p>
        {/if}
        {#if !dryRun.snapshot_found}
          <p class="mt-1 text-xs text-amber-600">{t("automation.dry_run_no_snapshot")}</p>
        {/if}
      </div>
    {/if}
  </section>

  {#if error}<p class="text-sm text-red-600">{t(error)}</p>{/if}
  <div class="flex justify-end gap-2">
    <a
      href="/settings/automation"
      class="rounded-lg border border-border px-4 py-2 text-sm text-text"
    >
      {t("common.cancel")}
    </a>
    <Button loading={busy.is("save")} disabled={busy.active}>
      {t("common.save")}
    </Button>
  </div>
</form>

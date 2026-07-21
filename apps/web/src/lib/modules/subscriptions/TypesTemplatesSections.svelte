<script lang="ts">
  /**
   * The subscription catalog: the tenant's standard subscriptions (the
   * `subscription_templates` presets, renamed in the UI only, #229) and types, as two tabs
   * (issue #142). Extracted from Instellingen → Abonnementen so the subscriptions page can
   * carry the same beheer without a trip through the menu; both hosts post to the shared
   * `manageActions` (manage.server.ts), so the action names below exist on either route.
   */
  import { Pencil, Power, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import {
    subscriptionTypeLabel,
    type SubscriptionTemplate,
    type SubscriptionType,
  } from "$lib/modules/subscriptions/types";

  let {
    types,
    templates,
    taskTemplates,
    locale,
    canManageTypes,
    canManageTemplates,
    error = null,
  }: {
    types: SubscriptionType[];
    templates: SubscriptionTemplate[];
    taskTemplates: { id: string; name: string }[];
    locale: string;
    canManageTypes: boolean;
    canManageTemplates: boolean;
    /** The host's `form?.error` — rendered inside whichever modal is open. */
    error?: string | null;
  } = $props();

  const INTERVALS = ["monthly", "quarterly", "yearly"] as const;

  // --- types ---------------------------------------------------------------- //
  let showTypeModal = $state(false);
  let editingType = $state<SubscriptionType | null>(null);
  let deleteTypeId = $state("");
  let confirmDeleteType = $state(false);
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActive = $state("");
  // The task templates this type spawns on first activation (#142) — chips + type-ahead.
  let spawnTemplates = $state<{ id: string; name: string }[]>([]);
  const spawnJson = $derived(JSON.stringify(spawnTemplates.map((tpl) => tpl.id)));
  const taskTemplateItems = $derived(
    taskTemplates
      .filter((tpl) => !spawnTemplates.some((s) => s.id === tpl.id))
      .map((tpl) => ({ value: tpl.id, label: tpl.name })),
  );

  function taskTemplateName(id: string): string {
    return taskTemplates.find((tpl) => tpl.id === id)?.name ?? "—";
  }
  function openTypeCreate() {
    editingType = null;
    spawnTemplates = [];
    showTypeModal = true;
  }
  function openTypeEdit(st: SubscriptionType) {
    editingType = st;
    spawnTemplates = (st.task_template_ids ?? []).map((id) => ({
      id,
      name: taskTemplateName(id),
    }));
    showTypeModal = true;
  }
  function requestToggle(st: SubscriptionType) {
    toggleId = st.id;
    toggleActive = String(!st.active);
    toggleForm?.requestSubmit();
  }

  // --- templates -------------------------------------------------------------- //
  let showTemplateModal = $state(false);
  let editingTemplate = $state<SubscriptionTemplate | null>(null);
  let deleteTemplateId = $state("");
  let confirmDeleteTemplate = $state(false);

  function openTemplateCreate() {
    editingTemplate = null;
    showTemplateModal = true;
  }
  function openTemplateEdit(tpl: SubscriptionTemplate) {
    editingTemplate = tpl;
    showTemplateModal = true;
  }

  const activeTypes = $derived(types.filter((st) => st.active));
  const typeLabel = (id: string | null | undefined) =>
    subscriptionTypeLabel(
      types.find((st) => st.id === id),
      locale,
    );
  const money = (value: string | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand";

  // Two tabs, same pill styling as the /overview sub-route tabs (#229) — local state, not
  // routes: the catalog is embedded in two hosts and neither owns a sub-path for it.
  let tab = $state<"templates" | "types">("templates");
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<!-- One row: the two catalog tabs left, the active tab's create action right (#229). -->
<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
  <div class="flex flex-wrap items-center gap-1">
    <button
      class={tabClass(tab === "templates")}
      aria-pressed={tab === "templates"}
      onclick={() => (tab = "templates")}
      >{t("settings.subscriptions.templates_heading")}</button
    >
    <button
      class={tabClass(tab === "types")}
      aria-pressed={tab === "types"}
      onclick={() => (tab = "types")}>{t("settings.subscriptions.types_heading")}</button
    >
  </div>
  {#if tab === "templates" && canManageTemplates}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
      onclick={openTemplateCreate}>{t("settings.subscriptions.new_template")}</button
    >
  {:else if tab === "types" && canManageTypes}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
      onclick={openTypeCreate}>{t("settings.subscriptions.new_type")}</button
    >
  {/if}
</div>

{#if tab === "types"}
  <!-- Types: the tenant's own categories (hosting, onderhoud, …) -->
  <section class="rounded-xl border border-border bg-surface-raised">
    {#if types.length === 0}
      <p class="p-6 text-sm text-text-muted">{t("settings.subscriptions.types_empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each types as st (st.id)}
          <li class="flex items-center gap-3 px-4 py-3 {st.active ? '' : 'opacity-50'}">
            <span class="flex-1 text-sm text-text">{subscriptionTypeLabel(st, locale)}</span>
            {#if (st.task_template_ids ?? []).length > 0}
              <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
                >{t("settings.subscriptions.spawn_count", {
                  count: (st.task_template_ids ?? []).length,
                })}</span
              >
            {/if}
            <span class="text-xs text-text-muted">{st.key}</span>
            {#if canManageTypes}
              <ActionsMenu
                items={[
                  { label: t("common.edit"), icon: Pencil, onclick: () => openTypeEdit(st) },
                  {
                    label: st.active ? t("common.deactivate") : t("common.activate"),
                    icon: Power,
                    onclick: () => requestToggle(st),
                  },
                  {
                    label: t("common.delete"),
                    icon: Trash2,
                    danger: true,
                    onclick: () => {
                      deleteTypeId = st.id;
                      confirmDeleteType = true;
                    },
                  },
                ]}
              />
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{:else}
  <!-- Standard subscriptions: named presets that prefill the create form -->
  <section class="rounded-xl border border-border bg-surface-raised">
    {#if templates.length === 0}
      <p class="p-6 text-sm text-text-muted">{t("settings.subscriptions.templates_empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each templates as tpl (tpl.id)}
          <li class="flex items-center gap-3 px-4 py-3">
            <span class="flex-1 text-sm text-text">{tpl.name}</span>
            {#if tpl.subscription_type_id}
              <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
                >{typeLabel(tpl.subscription_type_id)}</span
              >
            {/if}
            <span class="text-xs tabular-nums text-text-muted">
              {money(tpl.amount)} · {t(`subscriptions.interval.${tpl.interval}`)}
            </span>
            {#if canManageTemplates}
              <ActionsMenu
                items={[
                  { label: t("common.edit"), icon: Pencil, onclick: () => openTemplateEdit(tpl) },
                  {
                    label: t("common.delete"),
                    icon: Trash2,
                    danger: true,
                    onclick: () => {
                      deleteTemplateId = tpl.id;
                      confirmDeleteTemplate = true;
                    },
                  },
                ]}
              />
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<!-- Type create/edit -->
<Modal
  bind:open={showTypeModal}
  title={editingType ? t("settings.subscriptions.edit_type") : t("settings.subscriptions.new_type")}
>
  {#key editingType?.id ?? "new"}
    <form
      method="POST"
      action="?/saveType"
      class="space-y-4"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") showTypeModal = false;
          void update({ reset: false });
        }}
    >
      {#if editingType}<input type="hidden" name="id" value={editingType.id} />{/if}
      {#if !editingType}
        <div>
          <label for="st-key" class="mb-1 block text-sm text-text"
            >{t("settings.subscriptions.key")}</label
          >
          <input id="st-key" name="key" required pattern="[a-z0-9_]+" class={inputClass} />
          <p class="mt-1 text-xs text-text-muted">{t("settings.subscriptions.key_hint")}</p>
        </div>
      {/if}
      {#key editingType?.id ?? "new"}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={editingType?.label_i18n ?? {}}
          idPrefix="st"
        />
      {/key}
      <div>
        <span class="mb-1 block text-sm text-text"
          >{t("settings.subscriptions.task_templates")}</span
        >
        {#if spawnTemplates.length > 0}
          <div class="mb-2 flex flex-wrap gap-1.5">
            {#each spawnTemplates as tpl (tpl.id)}
              <span
                class="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-0.5 text-xs text-text"
              >
                {tpl.name}
                <button
                  type="button"
                  class="text-text-muted hover:text-red-600 dark:hover:text-red-400"
                  aria-label={t("common.delete")}
                  onclick={() => (spawnTemplates = spawnTemplates.filter((s) => s.id !== tpl.id))}
                  >✕</button
                >
              </span>
            {/each}
          </div>
        {/if}
        <Combobox
          items={taskTemplateItems}
          name="task_template_picker"
          id="st-task-templates"
          placeholder={t("settings.subscriptions.task_templates")}
          onselect={(value) => {
            if (value && !spawnTemplates.some((s) => s.id === value)) {
              spawnTemplates = [...spawnTemplates, { id: value, name: taskTemplateName(value) }];
            }
          }}
        />
        <input type="hidden" name="task_template_ids" value={spawnJson} />
        <p class="mt-1 text-xs text-text-muted">
          {t("settings.subscriptions.task_templates_help")}
        </p>
      </div>
      <input
        type="hidden"
        name="position"
        value={editingType?.position ?? types.length * 10 + 10}
      />
      {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showTypeModal = false)}>{t("common.cancel")}</button
        >
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

<!-- Template create/edit -->
<Modal
  bind:open={showTemplateModal}
  title={editingTemplate
    ? t("settings.subscriptions.edit_template")
    : t("settings.subscriptions.new_template")}
>
  {#key editingTemplate?.id ?? "new"}
    <form
      method="POST"
      action="?/saveTemplate"
      class="space-y-4"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") showTemplateModal = false;
          void update({ reset: false });
        }}
    >
      {#if editingTemplate}<input type="hidden" name="id" value={editingTemplate.id} />{/if}
      <div>
        <label for="tpl-name" class="mb-1 block text-sm text-text"
          >{t("subscriptions.field.name")}</label
        >
        <input
          id="tpl-name"
          name="name"
          required
          value={editingTemplate?.name ?? ""}
          class={inputClass}
        />
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="tpl-type" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.type")}</label
          >
          <select id="tpl-type" name="subscription_type_id" class={inputClass}>
            <option value="">—</option>
            {#each activeTypes as st (st.id)}
              <option value={st.id} selected={editingTemplate?.subscription_type_id === st.id}
                >{subscriptionTypeLabel(st, locale)}</option
              >
            {/each}
          </select>
        </div>
        <div>
          <label for="tpl-interval" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.interval")}</label
          >
          <select id="tpl-interval" name="interval" class={inputClass}>
            {#each INTERVALS as interval (interval)}
              <option
                value={interval}
                selected={(editingTemplate?.interval ?? "monthly") === interval}
                >{t(`subscriptions.interval.${interval}`)}</option
              >
            {/each}
          </select>
        </div>
        <div>
          <label for="tpl-amount" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.amount")}</label
          >
          <input
            id="tpl-amount"
            name="amount"
            type="number"
            min="0"
            step="0.01"
            value={editingTemplate?.amount ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="tpl-included" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.included_hours")}</label
          >
          <input
            id="tpl-included"
            name="included_hours"
            type="number"
            min="0"
            step="0.5"
            value={editingTemplate?.included_hours ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="tpl-notice" class="mb-1 block text-sm text-text"
            >{t("subscriptions.field.notice_period_days")}</label
          >
          <input
            id="tpl-notice"
            name="notice_period_days"
            type="number"
            min="0"
            max="365"
            value={editingTemplate?.notice_period_days ?? ""}
            class={inputClass}
          />
        </div>
      </div>
      <div>
        <label for="tpl-notes" class="mb-1 block text-sm text-text"
          >{t("subscriptions.field.notes")}</label
        >
        <textarea id="tpl-notes" name="notes" rows="2" class={inputClass}
          >{editingTemplate?.notes ?? ""}</textarea
        >
      </div>
      <input
        type="hidden"
        name="position"
        value={editingTemplate?.position ?? templates.length * 10 + 10}
      />
      {#if error}<p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showTemplateModal = false)}>{t("common.cancel")}</button
        >
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

<form bind:this={toggleForm} method="POST" action="?/toggleType" use:enhance class="hidden">
  <input type="hidden" name="id" value={toggleId} />
  <input type="hidden" name="active" value={toggleActive} />
</form>

<ConfirmDialog
  bind:open={confirmDeleteType}
  title={t("settings.subscriptions.delete_type")}
  message={t("settings.subscriptions.delete_type_confirm")}
  action="?/deleteType"
  fields={{ id: deleteTypeId }}
/>

<ConfirmDialog
  bind:open={confirmDeleteTemplate}
  title={t("settings.subscriptions.delete_template")}
  message={t("settings.subscriptions.delete_template_confirm")}
  action="?/deleteTemplate"
  fields={{ id: deleteTemplateId }}
/>

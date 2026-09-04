<script lang="ts">
  /**
   * One form for a recurring agreement — create and edit, from wherever it is mounted.
   *
   * It used to be the body of `/subscriptions`' modal and nothing else, so the only way to
   * record an agreement from a client's page was a link that carried the client through and
   * not the way back (#402's complaint about hours, one module over). Now the list page and a
   * client's `SubscriptionDialog` mount the same component, and the host decides three things:
   * what the pickers draw from (`lookups`), which client is preset, and which action names
   * the form and its quick-creates post to (`subscriptionActions`, `actions.server.ts`).
   *
   * Two rules worth keeping. **The links picker is narrowed to the agreement's client**: a
   * retainer's included hours are burned by that client's projects, and a picker offering every
   * project in the org was how a colleague linked another client's site by a similar name. A
   * project attached to no client stays on offer under every client (`projects/picker.ts`), and
   * whatever is already linked stays linked whichever client is chosen. And **picking a preset
   * fills in what the preset defines and leaves the rest alone** — re-seeding wholesale wiped
   * the client and the start date, which a preset has no opinion about.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import I18nLocaleSwitcher from "$lib/core/ui/I18nLocaleSwitcher.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import AutoInvoiceModeField from "$lib/modules/invoicing/AutoInvoiceModeField.svelte";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";

  import {
    subscriptionTypeLabel,
    type Subscription,
    type SubscriptionFormLookups,
    type SubscriptionTemplate,
  } from "./types";
  import {
    hasNoteVariables,
    noteVariableItems,
    notePlaceholder,
    resolveNoteVariables,
    subscriptionNoteValues,
  } from "./variables";

  let {
    editing = null,
    lookups,
    locale,
    defaultCompanyId = "",
    action,
    projectAction = "?/createProject",
    typeAction = "?/createType",
    companyAction = "?/createCompany",
    onsaved,
    oncancel,
  }: {
    /** The row being edited; `null` creates. The host keys the form per row. */
    editing?: Subscription | null;
    lookups: SubscriptionFormLookups;
    locale: string;
    /** Preselected in the client picker — a default that is visible and changeable. */
    defaultCompanyId?: string;
    /** Where the form posts: the host's create or update action. */
    action: string;
    /** The host's quick-create actions, for the pickers' "＋ … toevoegen". */
    projectAction?: string;
    typeAction?: string;
    companyAction?: string;
    onsaved?: () => void;
    oncancel?: () => void;
  } = $props();

  /** The host page's action result — the refusal to print, and what a quick-create made. */
  const result = $derived(
    page.form as {
      error?: string;
      qcError?: string;
      inlineCreated?: { slot: string; id: string; name?: string };
    } | null,
  );

  const busy = new InFlight();
  const canManageTypes = $derived(can(page.data.user, "subscriptions.type.manage"));

  // Inline creates from the pickers (#115, docs/UX.md — per-picker definition of done).
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcProjectOpen = $state(false);
  let qcProjectName = $state("");
  let qcTypeOpen = $state(false);
  let qcTypeName = $state("");

  // "Create from template" (#142): prefill, never a server-side copy — this form stays the
  // single validation path. Rekeys the form so the defaults re-read.
  let prefill = $state<SubscriptionTemplate | null>(null);

  // The fields a note's variables can draw on (#259), mirrored as reactive state so the edit
  // preview resolves live as you type. The picked company/type live here too, so an
  // inline-created one auto-selects and the nested project quick-create inherits the chosen
  // client (#247). Seeded once: the host keys this component per row, so props never swap.
  // svelte-ignore state_referenced_locally
  let pv = $state({
    name: editing?.name ?? "",
    companyId: editing?.company_id ?? defaultCompanyId,
    typeId: editing?.subscription_type_id ?? "",
    amount: String(editing?.amount ?? ""),
    interval: editing?.interval ?? "monthly",
    includedHours: String(editing?.included_hours ?? ""),
    startDate: editing?.start_date ?? "",
    notes: editing?.notes ?? "",
  });

  function applyTemplate(tpl: SubscriptionTemplate | null) {
    prefill = tpl;
    if (!tpl) return;
    pv.name = tpl.name;
    pv.typeId = tpl.subscription_type_id ?? "";
    pv.amount = String(tpl.amount ?? "");
    pv.interval = tpl.interval ?? "monthly";
    pv.includedHours = String(tpl.included_hours ?? "");
    pv.notes = tpl.notes ?? "";
  }

  // Projects linked to the agreement: time on these counts toward the bundle.
  // svelte-ignore state_referenced_locally
  let linkedProjects = $state<{ id: string; name: string }[]>(
    (editing?.links ?? [])
      .filter((l) => l.entity_type === "project")
      .map((l) => ({ id: l.entity_id, name: projectName(l.entity_id) })),
  );

  $effect(() => {
    const created = result?.inlineCreated;
    if (created?.slot === "company") pv.companyId = created.id;
    if (created?.slot === "subscription_type") pv.typeId = created.id;
    if (created?.slot === "project" && !linkedProjects.some((p) => p.id === created.id)) {
      linkedProjects = [
        ...linkedProjects,
        { id: created.id, name: created.name ?? projectName(created.id) },
      ];
    }
  });

  // An archived client sits behind the search; whatever is already picked stays on offer.
  const companyPicker = $derived(
    splitCompanyOptions(lookups.companies, { selectedId: pv.companyId }),
  );
  const companyItems = $derived(companyPicker.live);

  const STATUSES = ["draft", "active", "paused", "cancelled"] as const;
  const INTERVALS = ["monthly", "quarterly", "yearly"] as const;

  const activeTypes = $derived(lookups.types.filter((st) => st.active));
  const typeItems = $derived(
    activeTypes.map((st) => ({ value: st.id, label: subscriptionTypeLabel(st, locale) })),
  );
  function typeLabel(id: string | null | undefined): string {
    return subscriptionTypeLabel(
      lookups.types.find((st) => st.id === id),
      locale,
    );
  }

  // The preset an agreement came from, while it still carries that preset's name: renaming the
  // preset renames this row too, and giving it its own name here is how it stops following.
  const followedTemplate = $derived(
    editing?.subscription_template_id
      ? (lookups.templates.find(
          (tpl) => tpl.id === editing?.subscription_template_id && tpl.name === editing?.name,
        ) ?? null)
      : null,
  );

  // Narrowed to the agreement's client (the header comment says why). A finished project is
  // not something to *add* to a running agreement, so it drops behind the search wearing its
  // status; already-linked ones drop out entirely.
  const projectPicker = $derived(
    splitProjectOptions(
      lookups.projects.filter((p) => !linkedProjects.some((l) => l.id === p.id)),
      { selectedId: linkedProjects.map((l) => l.id), companyId: pv.companyId },
    ),
  );
  const projectItems = $derived(projectPicker.live);
  const linksJson = $derived(
    JSON.stringify(linkedProjects.map((p) => ({ entity_type: "project", entity_id: p.id }))),
  );

  function projectName(id: string): string {
    return lookups.projects.find((p) => p.id === id)?.name ?? "—";
  }

  // Note variables (#259): the note keeps its `{{company_name}}`-style tokens in storage. They
  // are resolved only for reading — a live preview while editing, an unknown value shown as a
  // `[label]` placeholder.
  const variableItems = $derived(noteVariableItems(t));
  const previewNotes = $derived(
    hasNoteVariables(pv.notes)
      ? resolveNoteVariables(
          pv.notes,
          subscriptionNoteValues({
            companyName: lookups.companies.find((c) => c.id === pv.companyId)?.name ?? null,
            subscriptionName: pv.name,
            typeLabel: pv.typeId ? typeLabel(pv.typeId) : null,
            amount: pv.amount,
            interval: pv.interval,
            includedHours: pv.includedHours,
            startDate: pv.startDate,
            brandName: page.data.theme?.brandName ?? null,
          }),
          { placeholder: notePlaceholder(t) },
        )
      : "",
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<!-- Prefill from a preset (#142). Outside the {#key} so picking one survives the rekey. -->
{#if !editing && lookups.templates.length > 0}
  <div class="mb-4">
    <label for="sub-template" class="mb-1 block text-sm font-medium text-text"
      >{t("subscriptions.from_template")}</label
    >
    <select
      id="sub-template"
      class={inputClass}
      value={prefill?.id ?? ""}
      onchange={(e) =>
        applyTemplate(lookups.templates.find((tpl) => tpl.id === e.currentTarget.value) ?? null)}
    >
      <option value="">—</option>
      {#each lookups.templates as tpl (tpl.id)}
        <option value={tpl.id}>{tpl.name}</option>
      {/each}
    </select>
  </div>
{/if}
{#key prefill?.id ?? ""}
  <form
    method="POST"
    {action}
    use:enhance={busy.wrap("save", () => ({ result: outcome, update }) => {
      if (outcome.type === "success") onsaved?.();
      // `keep`: a refusal leaves the typing in place, and a success unmounts the form anyway.
      void update({ reset: false });
    })}
    class="space-y-4"
  >
    {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
    <!-- Which preset this came from: provenance, so a later rename of the standard
         subscription reaches this agreement's (read-only, preset-owned) name. -->
    {#if !editing && prefill}
      <input type="hidden" name="subscription_template_id" value={prefill.id} />
    {/if}
    <div>
      <label for="sub-name" class="mb-1 block text-sm font-medium text-text"
        >{t("subscriptions.field.name")}</label
      >
      <input
        id="sub-name"
        name="name"
        required
        readonly={!editing && !!prefill}
        bind:value={pv.name}
        class="{inputClass} read-only:bg-surface read-only:text-text-muted"
      />
      {#if !editing && prefill}
        <p class="mt-1 text-xs text-text-muted">{t("subscriptions.name_from_template")}</p>
      {:else if followedTemplate}
        <p class="mt-1 text-xs text-text-muted">
          {t("subscriptions.follows_template", { name: followedTemplate.name })}
        </p>
      {/if}
    </div>
    <div>
      <label for="sub-company" class="mb-1 block text-sm font-medium text-text"
        >{t("subscriptions.field.company")}</label
      >
      <Combobox
        items={companyItems}
        archived={companyPicker.retired}
        archivedLabel={companyArchivedLabel()}
        name="company_id"
        bind:value={pv.companyId}
        id="sub-company"
        placeholder={t("subscriptions.field.company")}
        oncreate={(name) => {
          qcCompanyName = name;
          qcCompanyOpen = true;
        }}
      />
    </div>
    <div>
      <label for="sub-type" class="mb-1 block text-sm font-medium text-text"
        >{t("subscriptions.field.type")}</label
      >
      <Combobox
        items={typeItems}
        name="subscription_type_id"
        bind:value={pv.typeId}
        id="sub-type"
        placeholder={t("subscriptions.field.type")}
        oncreate={canManageTypes
          ? (name) => {
              qcTypeName = name;
              qcTypeOpen = true;
            }
          : undefined}
      />
    </div>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="sub-status" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.status")}</label
        >
        <select id="sub-status" name="status" class={inputClass}>
          {#each STATUSES as status (status)}
            <option value={status} selected={(editing?.status ?? "active") === status}
              >{t(`subscriptions.status.${status}`)}</option
            >
          {/each}
        </select>
      </div>
      <div>
        <label for="sub-interval" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.interval")}</label
        >
        <select id="sub-interval" name="interval" class={inputClass} bind:value={pv.interval}>
          {#each INTERVALS as interval (interval)}
            <option value={interval}>{t(`subscriptions.interval.${interval}`)}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="sub-amount" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.amount")}</label
        >
        <input
          id="sub-amount"
          name="amount"
          type="number"
          min="0"
          step="0.01"
          required={!editing}
          value={pv.amount}
          oninput={(e) => (pv.amount = e.currentTarget.value)}
          class={inputClass}
        />
      </div>
      <div>
        <label for="sub-included" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.included_hours")}</label
        >
        <input
          id="sub-included"
          name="included_hours"
          type="number"
          min="0"
          step="0.5"
          value={pv.includedHours}
          oninput={(e) => (pv.includedHours = e.currentTarget.value)}
          class={inputClass}
        />
      </div>
      <div>
        <label for="sub-start" class="mb-1 block text-sm font-medium text-text"
          >{t("subscriptions.field.start_date")}</label
        >
        <DateInput name="start_date" id="sub-start" required bind:value={pv.startDate} />
      </div>
      <!-- Edit only (#223): on create there is nothing to anchor a "next invoice" against —
           the API derives the first cycle boundary (start + one period) on activation. -->
      {#if editing}
        <div>
          <label for="sub-next" class="mb-1 block text-sm font-medium text-text"
            >{t("subscriptions.field.next_invoice")}</label
          >
          <DateInput
            name="next_invoice_date"
            id="sub-next"
            value={editing.next_invoice_date ?? ""}
          />
        </div>
      {/if}
    </div>
    <!-- How far the cycle cron takes this agreement's invoice. Asked here rather than
         inferred, because an agency automating twelve hosting retainers still assembles by
         hand the one client whose invoice is argued over every month, and per-org config
         cannot express that. "Follow the organisation setting" is the default. -->
    <AutoInvoiceModeField
      name="auto_invoice_mode"
      value={editing?.auto_invoice_mode ?? ""}
      inheritable
      orgMode={lookups.orgAutoInvoiceMode ?? "draft"}
    />
    <div>
      <span class="mb-1 block text-sm font-medium text-text"
        >{t("subscriptions.field.projects")}</span
      >
      {#if linkedProjects.length > 0}
        <div class="mb-2 flex flex-wrap gap-1.5">
          {#each linkedProjects as proj (proj.id)}
            <span
              class="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-0.5 text-xs text-text"
            >
              {proj.name}
              <button
                type="button"
                class="text-text-muted hover:text-red-600 dark:hover:text-red-400"
                aria-label={t("common.delete")}
                onclick={() => (linkedProjects = linkedProjects.filter((p) => p.id !== proj.id))}
                >✕</button
              >
            </span>
          {/each}
        </div>
      {/if}
      <Combobox
        items={projectItems}
        archived={projectPicker.retired}
        archivedLabel={projectArchivedLabel()}
        name="link_project_picker"
        id="sub-projects"
        placeholder={t("subscriptions.field.projects")}
        onselect={(value) => {
          if (value && !linkedProjects.some((p) => p.id === value)) {
            linkedProjects = [...linkedProjects, { id: value, name: projectName(value) }];
          }
        }}
        oncreate={(name) => {
          qcProjectName = name;
          qcProjectOpen = true;
        }}
      />
      <input type="hidden" name="links" value={linksJson} />
      <p class="mt-1 text-xs text-text-muted">
        {pv.companyId
          ? t("subscriptions.field.projects_help_client")
          : t("subscriptions.field.projects_help")}
      </p>
    </div>
    <div>
      <label for="sub-notes" class="mb-1 block text-sm font-medium text-text"
        >{t("subscriptions.field.notes")}</label
      >
      <RichTextEditor
        id="sub-notes"
        name="notes"
        rows={2}
        value={pv.notes}
        variables={variableItems}
        scope={{ companyId: pv.companyId || null }}
        onchange={(v) => (pv.notes = v)}
      />
      <p class="mt-1 text-xs text-text-muted">{t("subscriptions.variables.hint")}</p>
      {#if hasNoteVariables(pv.notes)}
        <div class="mt-2 rounded-lg border border-border bg-surface p-3">
          <p class="mb-1 text-xs font-medium text-text-muted">
            {t("subscriptions.variables.preview")}
          </p>
          <Markdown value={previewNotes} />
        </div>
      {/if}
    </div>
    {#if lookups.definitions.length > 0}
      <CustomFieldsForm
        definitions={lookups.definitions}
        values={editing?.custom ?? {}}
        {locale}
        scope={{ companyId: pv.companyId || null }}
      />
    {:else}
      <input type="hidden" name="custom" value={JSON.stringify(editing?.custom ?? {})} />
    {/if}
    {#if result?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(result.error)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => oncancel?.()}>{t("common.cancel")}</button
      >
      <Button loading={busy.is("save")} disabled={busy.active}>{t("common.save")}</Button>
    </div>
  </form>
{/key}

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  definitions={lookups.companyDefinitions}
  {locale}
  action={companyAction}
  error={result?.qcError ?? null}
/>

<!-- Inline project create from the links picker (docs/UX.md — per-picker definition of done). -->
<Modal bind:open={qcProjectOpen} title={t("time.quick_create.project")}>
  {#key qcProjectName + String(qcProjectOpen)}
    <form
      method="POST"
      action={projectAction}
      use:enhance={busy.wrap("qcProject", () => ({ result: outcome, update }) => {
        if (outcome.type === "success") qcProjectOpen = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <div>
        <label for="qc-sub-project-name" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.name")}</label
        >
        <input
          id="qc-sub-project-name"
          name="name"
          value={qcProjectName}
          required
          class={inputClass}
        />
      </div>
      <div>
        <label for="qc-sub-project-company" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.company")}</label
        >
        <!-- Required: a project belongs to a client. The agreement's client is the default. -->
        <Combobox
          items={companyItems}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          name="company_id"
          value={pv.companyId}
          id="qc-sub-project-company"
          allowEmpty={false}
          placeholder={t("projects.field.company")}
        />
      </div>
      {#if result?.qcError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(result.qcError)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (qcProjectOpen = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("qcProject")} disabled={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>

<!-- Inline subscription-type create from the picker (#142, docs/UX.md — per-picker rule).
     The full type dialog; the spawn list stays in Instellingen → Abonnementen. -->
<Modal bind:open={qcTypeOpen} title={t("settings.subscriptions.new_type")}>
  {#key qcTypeName + String(qcTypeOpen)}
    <form
      method="POST"
      action={typeAction}
      use:enhance={busy.wrap("qcType", () => ({ result: outcome, update }) => {
        if (outcome.type === "success") qcTypeOpen = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <I18nLocaleSwitcher />
      {#key qcTypeName}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={{ nl: qcTypeName }}
          idPrefix="qc-type"
        />
      {/key}
      {#if result?.qcError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(result.qcError)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (qcTypeOpen = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("qcType")} disabled={busy.active}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>

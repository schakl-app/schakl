<script lang="ts">
  import { Pencil, Trash2, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import type { BulkFieldDef } from "$lib/core/bulk/types";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import { can } from "$lib/core/permissions";
  import { formatPhone } from "$lib/core/phone";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import PhoneInput from "$lib/core/ui/PhoneInput.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { page } from "$app/state";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { CONTACT_COLUMNS } from "$lib/modules/contacts/columns";
  import { contactTypeLabel } from "$lib/modules/contacts/types";

  function typeHref(typeId: string): string {
    const params = new URLSearchParams(page.url.searchParams);
    if (typeId) params.set("type", typeId);
    else params.delete("type");
    const qs = params.toString();
    return qs ? `/contacts?${qs}` : "/contacts";
  }

  let { data, form } = $props();

  type Contact = (typeof data.contacts)[number];

  let showCreate = $state(false);
  let deleteId = $state("");
  let deleteName = $state("");
  let confirmDelete = $state(false);
  const busy = new InFlight();

  // Row actions render only for holders of the matching permission (#253).
  const canWrite = $derived(can(page.data.user, "contacts.contact.write"));
  const canDelete = $derived(can(page.data.user, "contacts.contact.delete"));
  // `company_ids` on the create call is a `contacts.link.write` act — the API demands it up front
  // rather than rolling the contact back — so the "Verbonden klanten" picker carries that key and
  // not the one the rest of this form carries (#310). Without it the contact is created unlinked,
  // which is a real thing to do; with the picker drawn it was a 403 on submit and a lost form.
  const canLink = $derived(can(page.data.user, "contacts.link.write"));
  // Third key again: "＋ … toevoegen" in that picker creates a *client*.
  const canWriteCompany = $derived(can(page.data.user, "companies.company.write"));

  // Client filter (#154) — the tasks page's URL-param shape; the API applies it.
  const companyFilterItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  function setFilter(key: string, value: string) {
    const url = resetPage(new URL(page.url));
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // --- bulk (the ✎ selection mode in the toolbar) --------------------------------------
  // Only the client link, and only in the attaching direction. Someone's name, address and phone
  // are the definition of that person and are never shared by a selection; "these six all work at
  // Acme now" is the one thing a batch of contacts genuinely has in common. Detaching is not
  // offered here — from the same control it is the one that gets misclicked, so it stays on the
  // contact, where you can see which link you are breaking. Mirrors
  // `apps/api/app/modules/contacts/bulk.py`; the label is the import's, so the two surfaces that
  // name this column can never name it differently.
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const bulkFields: BulkFieldDef[] = $derived([
    {
      key: "company",
      label: t("impex.column.contact.company"),
      type: "fk",
      // The clients the page already loaded for its own filter — the dialog costs no extra call.
      options: companyFilterItems,
    },
  ]);
  // One configuration, spread into the ✎ in the toolbar and the strip above the table: they
  // render in different places and must never disagree about what this list can do.
  const bulkConfig = $derived({
    fields: bulkFields,
    writePermission: "contacts.contact.write",
    deletePermission: "contacts.contact.delete",
    deleteMessage: t("contacts.bulk.delete_message", { count: bulkSelected.length }),
    fieldErrors: form?.bulkFields ?? null,
  });

  // #80: companies to link while creating the contact. `ContactCreate.company_ids` does the
  // linking server-side (the first becomes that company's primary contact), so the picker only
  // has to collect IDs and serialise them into one hidden field.
  let linkedCompanyIds = $state<string[]>([]);
  let companyPick = $state("");
  const companyCandidates = $derived(
    data.companies
      .filter((c) => !linkedCompanyIds.includes(c.id))
      .map((c) => ({ value: c.id, label: c.name })),
  );
  const companyLabel = (id: string) => data.companies.find((c) => c.id === id)?.name ?? id;
  function addCompany(id: string) {
    if (id && !linkedCompanyIds.includes(id)) linkedCompanyIds = [...linkedCompanyIds, id];
    companyPick = "";
  }
  function removeCompany(id: string) {
    linkedCompanyIds = linkedCompanyIds.filter((x) => x !== id);
  }

  // Inline-create from the picker (#115): "＋ … toevoegen" opens the full client dialog.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  // Apply each `inlineCreated` once (the PartyPicker rule): the new client joins the chips
  // exactly like a picked one, and removing it later must never re-add it.
  let qcApplied = $state<{ slot: string; id: string } | null>(null);
  $effect(() => {
    const created = form?.inlineCreated;
    if (!created || created === qcApplied) return;
    if (created.slot === "company") {
      addCompany(created.id);
      qcApplied = created;
    }
  });

  function fullName(c: { first_name: string; last_name?: string | null }) {
    return [c.first_name, c.last_name].filter(Boolean).join(" ");
  }

  function confirmDeleteOf(contact: Contact) {
    deleteId = contact.id;
    deleteName = fullName(contact);
    confirmDelete = true;
  }

  // Custom fields join the built-ins as selectable columns with no code here (#24).
  const allColumns = $derived([
    ...CONTACT_COLUMNS,
    ...customFieldColumns(data.definitions, data.locale),
  ]);

  // --- grouped by client -----------------------------------------------------
  // The sections are the clients *on this page*, alphabetically, with the unattached people
  // last. Built from the rows rather than from the client picker: 200 sections, 190 of them
  // empty, is not a list of contacts.
  //
  // A person linked to several clients is listed under **each** of them — one record drawn
  // where you would look for it, not a record forced to pick a home. That is why the `companies`
  // column stays: from inside the Acme section it is the only thing that says this person also
  // sits under Globex. It loses its `sortKey` instead (docs/UX.md: a sort orders rows within a
  // section and never reorders the sections, so sorting by client would visibly do nothing).
  const NO_COMPANY = "__no_company";
  const groups = $derived.by(() => {
    // A plain record, not a Map: `svelte/prefer-svelte-reactivity` rejects a mutated Map even
    // in a derived, and this one is a throwaway index rather than state.
    const named: Record<string, string> = {};
    let unattached = false;
    for (const contact of data.contacts) {
      const links = contact.companies ?? [];
      if (links.length === 0) unattached = true;
      for (const link of links) named[link.company_id] = link.name;
    }
    const sections = Object.entries(named)
      .map(([key, label]) => ({ key, label, collapsible: true }))
      .sort((a, b) => a.label.localeCompare(b.label, data.locale));
    if (unattached)
      sections.push({
        key: NO_COMPANY,
        label: t("contacts.group.no_company"),
        collapsible: true,
      });
    return sections;
  });

  const groupOf = (contact: Contact): string | string[] =>
    contact.companies?.length ? contact.companies.map((link) => link.company_id) : NO_COMPANY;

  const table = createTableLayout<Contact>({
    all: () => allColumns,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      companies: companiesCell,
      email: emailCell,
      phone: phoneCell,
      job_title: jobCell,
      created_at: createdCell,
    }),
  });
</script>

{#snippet nameCell(contact: Contact)}
  <!-- `block`, because `overflow` does not apply to an inline box: a bare `truncate` on an `<a>`
       gets only its `nowrap` half, so under the table's fixed layout a long name runs sideways
       over the next column instead of ellipsizing inside its own. Same for every cell below. -->
  <a href="/contacts/{contact.id}" class="block truncate font-medium text-text hover:text-brand"
    >{fullName(contact)}</a
  >
{/snippet}

{#snippet companiesCell(contact: Contact)}
  {#if contact.companies && contact.companies.length > 0}
    <!-- `flex-nowrap`, not `wrap`: wrapping is what makes a client with a long name a two-line
         chip and a four-line row. The chips shrink and ellipsize instead, and the full name is
         on the hover title. -->
    <span class="flex min-w-0 flex-nowrap gap-1 overflow-hidden">
      {#each contact.companies as link (link.company_id)}
        <!-- Colour is the marker: the client this person is the primary contact for is
             brand-coloured, never starred (docs/UX.md). -->
        <a
          href="/companies/{link.company_id}"
          title={link.name}
          class="truncate rounded-full px-2 py-0.5 text-xs {link.is_primary
            ? 'bg-brand/10 text-brand ring-1 ring-inset ring-brand/30'
            : 'bg-surface text-text-muted'} hover:text-brand"
        >
          {link.name}
          {#if link.is_primary}<span class="sr-only">({t("contacts.primary")})</span>{/if}
        </a>
      {/each}
    </span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet emailCell(contact: Contact)}
  {#if contact.email}
    <a href="mailto:{contact.email}" class="block truncate text-text-muted hover:text-brand"
      >{contact.email}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet phoneCell(contact: Contact)}
  {#if contact.phone}
    <a href="tel:{contact.phone}" class="block truncate text-text-muted hover:text-brand"
      >{formatPhone(contact.phone)}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet jobCell(contact: Contact)}
  <span class="block truncate text-text-muted">{contact.job_title || "—"}</span>
{/snippet}

{#snippet createdCell(contact: Contact)}
  <span class="text-text-muted">{fmtNumericDate(contact.created_at.slice(0, 10))}</span>
{/snippet}

{#snippet rowActions(contact: Contact)}
  <ActionsMenu
    items={[
      ...(canWrite
        ? [{ label: t("common.edit"), icon: Pencil, href: editHref(`/contacts/${contact.id}`) }]
        : []),
      ...(canDelete
        ? [
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => confirmDeleteOf(contact),
            },
          ]
        : []),
    ]}
  />
{/snippet}

{#snippet mobileRow(contact: Contact)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href="/contacts/{contact.id}" class="min-w-0 flex-1">
      <span class="block truncate font-medium text-text">{fullName(contact)}</span>
      {#if contact.email}
        <span class="mt-0.5 block truncate text-sm text-text-muted">{contact.email}</span>
      {/if}
    </a>
    {#if canWrite || canDelete}
      {@render rowActions(contact)}
    {/if}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("contacts.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("contacts.empty_hint")}</p>
  </div>
{/snippet}

<svelte:head>
  <title>{pageTitle(navLabel("contacts", t("contacts.title")))}</title>
</svelte:head>

<!-- Wraps: "Nieuwe contactpersoon" is a 192px button, and a phone has ~312px of content width
     once the title has had its share. The Dutch label is the long one, so English never shows it. -->
<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{navLabel("contacts", t("contacts.title"))}</h1>
  {#if canWrite}
    <!-- Opening the inline create form is a contacts.contact.write act; hidden from a read-only
         portal client (#244), like the row edit/delete actions and the Import button below. -->
    <button
      class="shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      onclick={() => (showCreate = !showCreate)}
    >
      {t("contacts.new")}
    </button>
  {/if}
</div>

<!-- Search + the personal column picker, on their own wrapping row (issue #36): title, a fixed
     224px search box, the picker and the primary action on one unwrappable line have a
     min-content width no phone has. This is the shape `companies` already uses. -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <SearchInput />
  <div class="w-44">
    <Combobox
      items={companyFilterItems}
      name="_filter_company"
      value={data.companyFilter}
      placeholder={t("contacts.filter.company")}
      onselect={(v) => setFilter("company", v)}
      id="filter-company"
    />
  </div>
  <div class="ml-auto flex flex-wrap items-center gap-2">
    <!-- The Export link carries the page's current filters, so the file holds exactly the
         filtered list on screen — the whole set, not just the loaded page (issue #77). -->
    <ImpexBar
      entity="contact"
      readPermission="contacts.contact.read"
      writePermission="contacts.contact.write"
      filters={{
        q: page.url.searchParams.get("q"),
        company_id: data.companyFilter,
        sort: data.table.sort,
      }}
      locale={data.locale}
      {form}
    />
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
    <!-- Last in the toolbar, always: it is the only control here that changes what the *rows*
         do rather than what the list shows, so it sits after Kolommen rather than among the
         list's own controls. Pressing it opens the selection strip above the table. -->
    <BulkToggle bind:selecting bind:selected={bulkSelected} {...bulkConfig} />
  </div>
</div>

{#if data.types.length > 0}
  <div class="mb-4 flex flex-wrap gap-1.5">
    <a
      href={typeHref("")}
      class="rounded-full border px-3 py-1 text-xs
        {data.typeFilter === ''
        ? 'border-brand bg-brand/10 font-medium text-brand'
        : 'border-border text-text-muted hover:text-text'}">{t("contacts.all_types")}</a
    >
    {#each data.types as ct (ct.id)}
      <a
        href={typeHref(ct.id)}
        class="rounded-full border px-3 py-1 text-xs
          {data.typeFilter === ct.id
          ? 'border-brand bg-brand/10 font-medium text-brand'
          : 'border-border text-text-muted hover:text-text'}">{contactTypeLabel(ct, data.locale)}</a
      >
    {/each}
  </div>
{/if}

{#if showCreate}
  <form
    method="POST"
    action="?/create"
    use:enhance={busy.wrap("", () => ({ result, update }) => {
      // Close only on success: a 409 (duplicate email) must stay visible in the form.
      if (result.type === "success") {
        showCreate = false;
        linkedCompanyIds = [];
      }
      void update({ reset: false });
    })}
    class="mb-6 rounded-xl border border-border bg-surface-raised p-4"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="first_name" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.first_name")}
        </label>
        <input
          id="first_name"
          name="first_name"
          required
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="last_name" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.last_name")}
        </label>
        <input
          id="last_name"
          name="last_name"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.email")}
        </label>
        <input
          id="email"
          name="email"
          type="email"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="phone" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.phone")}
        </label>
        <PhoneInput id="phone" name="phone" />
      </div>
      <div class="sm:col-span-2">
        <label for="job_title" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.job_title")}
        </label>
        <input
          id="job_title"
          name="job_title"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>

      <!-- #80: link one or more clients at creation instead of a second step afterwards. The
           first company linked becomes its primary contact (API behaviour). -->
      {#if canLink}
        <div class="sm:col-span-2">
          <span class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.connected_companies")}</span
          >
          <input type="hidden" name="company_ids" value={JSON.stringify(linkedCompanyIds)} />
          {#if linkedCompanyIds.length > 0}
            <ul class="mb-2 flex flex-wrap gap-2">
              {#each linkedCompanyIds as id (id)}
                <li
                  class="inline-flex items-center gap-1.5 rounded-full bg-surface py-1 pl-2.5 pr-1.5 text-sm text-text"
                >
                  <span class="font-medium">{companyLabel(id)}</span>
                  <button
                    type="button"
                    class="rounded-full p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
                    title={t("contacts.unlink")}
                    aria-label={t("contacts.unlink")}
                    onclick={() => removeCompany(id)}><X size={14} /></button
                  >
                </li>
              {/each}
            </ul>
          {/if}
          <Combobox
            items={companyCandidates}
            name="_company_pick"
            bind:value={companyPick}
            id="contact-companies"
            placeholder={t("contacts.add_client")}
            allowEmpty={false}
            onselect={addCompany}
            keepOpenOnSelect
            oncreate={canWriteCompany
              ? (name) => {
                  qcCompanyName = name;
                  qcCompanyOpen = true;
                }
              : undefined}
          />
        </div>
      {/if}
    </div>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-border pt-4">
        <CustomFieldsForm definitions={data.definitions} locale={data.locale} />
      </div>
    {/if}

    {#if form?.error}
      <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="mt-4 flex gap-2">
      <Button loading={busy.active}>
        {t("common.save")}
      </Button>
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (showCreate = false)}
      >
        {t("common.cancel")}
      </button>
    </div>
  </form>
{/if}

{#if data.total > data.paging.limit}
  <!-- Sectioned by client, "Acme (2)" above a client that has seven contacts reads as the whole
       answer. The pager below says which slice this is, but the *group counts* still need saying
       out loud — a cap is reported, never silent (docs/PERFORMANCE.md). -->
  <p class="mb-3 text-sm text-text-muted">{t("contacts.groups_page_only")}</p>
{/if}

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={data.contacts}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  {groups}
  groupBy={groupOf}
  collapsed={table.collapsed}
  oncollapse={table.onCollapse}
  rowHref={(contact) => `/contacts/${contact.id}`}
  actions={canWrite || canDelete ? rowActions : undefined}
  {mobileRow}
  empty={emptyState}
  {selecting}
  bind:selected={bulkSelected}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("contacts.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  pickerSlot="company"
  definitions={data.companyDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>

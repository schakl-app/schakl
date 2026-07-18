<script lang="ts">
  import { Download, Pencil, Trash2, Upload, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ImportCsvModal from "$lib/core/impex/ImportCsvModal.svelte";
  import { can } from "$lib/core/permissions";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { page } from "$app/state";
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
  let showImport = $state(false);

  // The Export link carries the page's current filters, so the file holds exactly the
  // filtered list on screen — the whole set, not just the loaded page (issue #77).
  const exportHref = $derived.by(() => {
    const params = new URLSearchParams();
    const q = page.url.searchParams.get("q");
    if (q) params.set("q", q);
    if (data.companyFilter) params.set("company", data.companyFilter);
    if (data.table.sort) params.set("sort", data.table.sort);
    const query = params.toString();
    return `/contacts/export${query ? `?${query}` : ""}`;
  });

  // Client filter (#154) — the tasks page's URL-param shape; the API applies it.
  const companyFilterItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

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
  <a href="/contacts/{contact.id}" class="font-medium text-text hover:text-brand"
    >{fullName(contact)}</a
  >
{/snippet}

{#snippet companiesCell(contact: Contact)}
  {#if contact.companies && contact.companies.length > 0}
    <span class="flex flex-wrap gap-1">
      {#each contact.companies as link (link.company_id)}
        <!-- Colour is the marker: the client this person is the primary contact for is
             brand-coloured, never starred (docs/UX.md). -->
        <a
          href="/companies/{link.company_id}"
          class="rounded-full px-2 py-0.5 text-xs {link.is_primary
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
    <a href="mailto:{contact.email}" class="text-text-muted hover:text-brand">{contact.email}</a>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet phoneCell(contact: Contact)}
  {#if contact.phone}
    <a href="tel:{contact.phone}" class="text-text-muted hover:text-brand">{contact.phone}</a>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet jobCell(contact: Contact)}
  <span class="text-text-muted">{contact.job_title || "—"}</span>
{/snippet}

{#snippet createdCell(contact: Contact)}
  <span class="text-text-muted">{fmtNumericDate(contact.created_at.slice(0, 10))}</span>
{/snippet}

{#snippet rowActions(contact: Contact)}
  <ActionsMenu
    items={[
      { label: t("common.edit"), icon: Pencil, href: editHref(`/contacts/${contact.id}`) },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => confirmDeleteOf(contact),
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(contact: Contact)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href="/contacts/{contact.id}" class="min-w-0 flex-1">
      <span class="font-medium text-text">{fullName(contact)}</span>
      {#if contact.email}
        <span class="mt-0.5 block truncate text-sm text-text-muted">{contact.email}</span>
      {/if}
    </a>
    {@render rowActions(contact)}
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
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("contacts", t("contacts.title"))}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("contacts.count", { count: data.total })}</p>
  </div>
  <button
    class="shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("contacts.new")}
  </button>
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
    <!-- A plain link: the browser downloads through its own session (issue #77). -->
    <a
      href={exportHref}
      data-sveltekit-reload
      data-sveltekit-preload-data="off"
      class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
    >
      <Download class="h-4 w-4" />
      {t("impex.export")}
    </a>
    {#if can(page.data.user, "contacts.contact.write")}
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
        onclick={() => (showImport = true)}
      >
        <Upload class="h-4 w-4" />
        {t("impex.import")}
      </button>
    {/if}
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
  </div>
</div>

<ImportCsvModal
  bind:open={showImport}
  report={form?.impex ?? null}
  error={form?.impexError ?? null}
/>

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
    use:enhance={() =>
      ({ update }) => {
        void update().then(() => {
          showCreate = false;
          linkedCompanyIds = [];
        });
      }}
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
        <input
          id="phone"
          name="phone"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
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
        />
      </div>
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
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
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

<DataTable
  rows={data.contacts}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  rowHref={(contact) => `/contacts/${contact.id}`}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("contacts.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>

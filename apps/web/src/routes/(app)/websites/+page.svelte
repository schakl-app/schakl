<script lang="ts">
  /**
   * Websites overview (owner request): every client website in one list — a website is a
   * 0/1 child of a domain, so creating one here is connecting it to a domain. The detail
   * surface stays the domain page (#94); rows link through to it.
   */
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { components } from "$lib/core/api/schema";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { customFieldColumns } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DomainQuickCreate from "$lib/modules/domains/DomainQuickCreate.svelte";
  import HostingQuickCreate from "$lib/modules/hosting/HostingQuickCreate.svelte";
  import { WEBSITE_COLUMNS } from "$lib/modules/websites/columns";

  type Website = components["schemas"]["WebsiteRead"];

  let { data, form } = $props();

  // Deep link from the client page (?new=1&company=): the dialog opens with the domain
  // options narrowed to that client's domains.
  let showModal = $state(page.url.searchParams.has("new"));
  const initialCompanyId = page.url.searchParams.get("company") ?? "";
  let editing = $state<Website | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  const busy = new InFlight();

  // Row actions render only for holders of the matching permission (#253).
  const canWrite = $derived(can(page.data.user, "websites.website.write"));
  const canDelete = $derived(can(page.data.user, "websites.website.delete"));

  // Inline-create over the modal (#115): full dialogs, prefilled with what was typed.
  let qcHostingOpen = $state(false);
  let qcHostingName = $state("");
  let qcDomainOpen = $state(false);
  let qcDomainName = $state("");
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcCompanySlot = $state("company");
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
  let qcContactSlot = $state("contact");
  // One provider dialog serves both nested forms — the hosting dialog's provider picker and
  // the domain dialog's registrar/DNS/email pickers — so it carries the kind that asked.
  let qcProviderOpen = $state(false);
  let qcProviderName = $state("");
  let qcProviderKind = $state<"registrar" | "dns" | "email" | "hosting">("hosting");

  function quickCreateCompany(name: string, slot = "company") {
    qcCompanyName = name;
    qcCompanySlot = slot;
    qcCompanyOpen = true;
  }
  function quickCreateContact(name: string, slot: string) {
    qcContactName = name;
    qcContactSlot = slot;
    qcContactOpen = true;
  }
  function quickCreateProvider(kind: "registrar" | "dns" | "email" | "hosting", name: string) {
    qcProviderKind = kind;
    qcProviderName = name;
    qcProviderOpen = true;
  }

  // A domain carries at most one website, so the picker offers only unclaimed domains.
  const takenDomainIds = $derived(new Set(data.websites.map((w) => w.domain_id)));
  const domainItems = $derived(
    data.domains
      .filter((d) => !takenDomainIds.has(d.id))
      .filter((d) => !initialCompanyId || d.company_id === initialCompanyId)
      .map((d) => ({ value: d.id, label: d.name })),
  );
  const hostingItems = $derived(data.hosting.map((h) => ({ value: h.id, label: h.name })));
  // Inline-created records auto-select per slot and *stay* selected (#115): remembered in a
  // map, because `form.inlineCreated` only holds the latest create — a derived read straight
  // off it would clear the domain the moment a hosting account is quick-created after it.
  // A domain created from the picker: the refreshed load re-lists it as unclaimed, so the
  // Combobox resolves the id to its label.
  let createdBySlot = $state<Record<string, string>>({});
  $effect(() => {
    const created = form?.inlineCreated;
    if (created?.id && createdBySlot[created.slot] !== created.id) {
      createdBySlot = { ...createdBySlot, [created.slot]: created.id };
    }
  });
  const hostingCreated = $derived(createdBySlot["hosting_account"] ?? "");
  const domainCreated = $derived(createdBySlot["domain"] ?? "");

  // The technical owner offers exactly two choices — the agency or the client — labelled
  // with their actual names. The client is the picked domain's company, so the picker's
  // label follows the domain selection; before a domain is picked it reads "Deze klant".
  let selectedDomainId = $state("");
  $effect(() => {
    if (domainCreated) selectedDomainId = domainCreated;
  });
  const ownerCompanyName = $derived.by(() => {
    if (editing) return editing.company_name ?? "";
    const domain = data.domains.find((d) => d.id === selectedDomainId);
    return data.companies.find((c) => c.id === domain?.company_id)?.name ?? "";
  });

  // Radio selection is component state, never a one-way checked (docs/UX.md).
  let hostChoice = $state<"root" | "www">("root");

  function openCreate() {
    editing = null;
    hostChoice = "root";
    createdBySlot = {};
    selectedDomainId = "";
    showModal = true;
  }
  function openEdit(w: Website) {
    editing = w;
    hostChoice = w.root ? "root" : "www";
    createdBySlot = {};
    selectedDomainId = "";
    showModal = true;
  }
  function requestDelete(id: string) {
    deleteId = id;
    confirmDelete = true;
  }

  // The tenant's custom fields join the built-ins as selectable columns with no code here (#24).
  // Layout resolution and persistence are the shared table layout's job.
  const allColumns = $derived([
    ...WEBSITE_COLUMNS,
    ...customFieldColumns(data.definitions, data.locale),
  ]);

  const table = createTableLayout<Website>({
    all: () => allColumns,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      name: nameCell,
      company: companyCell,
      hosting: hostingCell,
      technical_owner: ownerCell,
      uptime: uptimeCell,
      created_at: createdCell,
    }),
  });
</script>

{#snippet nameCell(site: Website)}
  <!-- The detail surface stays the domain page (#94); the row links through to it. -->
  <a href={`/domains/${site.domain_id}#website`} class="font-medium text-text hover:text-brand">
    {site.root ? site.domain_name : `www.${site.domain_name}`}
  </a>
{/snippet}

{#snippet companyCell(site: Website)}
  <span class="text-text-muted">{site.company_name ?? "—"}</span>
{/snippet}

{#snippet hostingCell(site: Website)}
  <span class="text-text-muted">{site.hosting_name ?? "—"}</span>
{/snippet}

{#snippet ownerCell(site: Website)}
  <span class="text-text-muted">{site.technical_owner?.label || "—"}</span>
{/snippet}

{#snippet uptimeCell(site: Website)}
  {#if site.uptime_enabled}
    <span
      class="rounded-full bg-green-500/10 px-2 py-0.5 text-[11px] text-green-700 dark:text-green-400"
    >
      {t("websites.uptime_short")}
    </span>
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet createdCell(site: Website)}
  <span class="text-text-muted">{fmtNumericDate(site.created_at.slice(0, 10))}</span>
{/snippet}

{#snippet rowActions(site: Website)}
  <ActionsMenu
    items={[
      ...(canWrite
        ? [{ label: t("common.edit"), icon: Pencil, onclick: () => openEdit(site) }]
        : []),
      ...(canDelete
        ? [
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => requestDelete(site.id),
            },
          ]
        : []),
    ]}
  />
{/snippet}

{#snippet mobileRow(site: Website)}
  <!-- A phone gets the concept's row, not a sideways-scrolling grid (docs/UX.md). -->
  <div class="flex items-center gap-3">
    <a href={`/domains/${site.domain_id}#website`} class="min-w-0 flex-1">
      <span class="block truncate font-medium text-text">
        {site.root ? site.domain_name : `www.${site.domain_name}`}
      </span>
      {#if site.company_name}
        <span class="mt-0.5 block truncate text-sm text-text-muted">{site.company_name}</span>
      {/if}
    </a>
    {#if site.uptime_enabled}
      <span
        class="shrink-0 rounded-full bg-green-500/10 px-2 py-0.5 text-[11px] text-green-700 dark:text-green-400"
      >
        {t("websites.uptime_short")}
      </span>
    {/if}
    {#if canWrite || canDelete}
      {@render rowActions(site)}
    {/if}
  </div>
{/snippet}

{#snippet emptyState()}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("websites.empty")}</p>
  </div>
{/snippet}

<svelte:head>
  <title>{pageTitle(navLabel("websites", t("nav.websites")))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-2">
  <div>
    <h1 class="text-xl font-semibold text-text">{navLabel("websites", t("nav.websites"))}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("websites.count", { count: data.total })}</p>
  </div>
  {#if can(page.data.user, "websites.website.write")}
    <button
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
      onclick={openCreate}>{t("websites.new")}</button
    >
  {/if}
</div>

<!-- The personal column picker: every sort is reachable from here too (docs/UX.md). -->
<div class="mb-4 flex items-center justify-end">
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

<DataTable
  rows={data.websites}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  definitions={data.definitions}
  locale={data.locale}
  rowHref={(site) => `/domains/${site.domain_id}#website`}
  actions={canWrite || canDelete ? rowActions : undefined}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

{#if canWrite}
  <Modal bind:open={showModal} title={editing ? t("websites.edit") : t("websites.new")}>
    {#key editing?.id ?? "new"}
      <form
        method="POST"
        action="?/save"
        use:enhance={busy.wrap("", () => ({ result, update }) => {
          if (result.type === "success") showModal = false;
          void update({ reset: false });
        })}
      >
        {#if editing}<input type="hidden" name="website_id" value={editing.id} />{/if}
        <div class="space-y-4">
          {#if editing}
            <p class="text-sm text-text">
              <span class="text-text-muted">{t("websites.field.domain")}:</span>
              {editing.domain_name}
            </p>
          {:else}
            <div>
              <label for="website-domain" class="mb-1 block text-sm text-text"
                >{t("websites.field.domain")}</label
              >
              <Combobox
                items={domainItems}
                name="domain_id"
                bind:value={selectedDomainId}
                id="website-domain"
                placeholder={t("websites.field.domain")}
                oncreate={(name) => {
                  qcDomainName = name;
                  qcDomainOpen = true;
                }}
              />
              <p class="mt-1 text-xs text-text-muted">{t("websites.domain_hint")}</p>
            </div>
          {/if}
          <div>
            <span class="mb-1 block text-sm text-text">{t("websites.host")}</span>
            <div class="flex gap-3">
              <label class="flex items-center gap-1.5 text-sm text-text">
                <input type="radio" name="root" value="root" bind:group={hostChoice} /> @ (root)
              </label>
              <label class="flex items-center gap-1.5 text-sm text-text">
                <input type="radio" name="root" value="www" bind:group={hostChoice} />
                www
              </label>
            </div>
          </div>
          <div>
            <span class="mb-1 block text-sm text-text">{t("websites.technical_owner")}</span>
            <PartyPicker
              name="technical_owner"
              value={editing?.technical_owner ?? { type: "agency", id: null }}
              agencyLabel={data.agencyLabel}
              companies={data.companies}
              employees={data.employees}
              contacts={data.contacts}
              types={["agency", "company"]}
              typeLabels={{
                agency: data.agencyLabel,
                company: ownerCompanyName || undefined,
              }}
              companyPickable={false}
              id="website-owner"
              oncreatecompany={quickCreateCompany}
              oncreatecontact={quickCreateContact}
              created={form?.inlineCreated ?? null}
            />
          </div>
          <div>
            <label for="website-hosting" class="mb-1 block text-sm text-text"
              >{t("websites.hosting")}</label
            >
            <Combobox
              items={hostingItems}
              name="hosting_id"
              value={hostingCreated || (editing?.hosting_id ?? "")}
              id="website-hosting"
              placeholder={t("common.none")}
              oncreate={(name) => {
                qcHostingName = name;
                qcHostingOpen = true;
              }}
            />
          </div>
          <label class="flex items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              name="uptime_enabled"
              value="on"
              checked={editing?.uptime_enabled ?? false}
            />
            {t("websites.uptime")}
          </label>
          {#if data.definitions.length > 0}
            <CustomFieldsForm
              definitions={data.definitions}
              values={editing?.custom ?? {}}
              locale={data.locale}
            />
          {:else}
            <input type="hidden" name="custom" value={JSON.stringify(editing?.custom ?? {})} />
          {/if}
        </div>
        {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">
            {t(form.error)}
          </p>{/if}
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm text-text"
            onclick={() => (showModal = false)}>{t("common.cancel")}</button
          >
          <Button loading={busy.active}>
            {editing ? t("common.save") : t("websites.add")}
          </Button>
        </div>
      </form>
    {/key}
  </Modal>
{/if}

<HostingQuickCreate
  bind:open={qcHostingOpen}
  name={qcHostingName}
  companies={data.companies}
  providers={data.providers}
  employees={data.employees}
  contacts={data.contacts}
  agencyLabel={data.agencyLabel}
  definitions={data.hostingDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
  oncreatecompany={quickCreateCompany}
  oncreatecontact={quickCreateContact}
  oncreateprovider={quickCreateProvider}
  created={form?.inlineCreated ?? null}
/>
<DomainQuickCreate
  bind:open={qcDomainOpen}
  name={qcDomainName}
  companies={data.companies}
  providers={data.providers}
  employees={data.employees}
  contacts={data.contacts}
  agencyLabel={data.agencyLabel}
  definitions={data.domainDefinitions}
  locale={data.locale}
  {initialCompanyId}
  error={form?.qcError ?? null}
  oncreatecompany={quickCreateCompany}
  oncreatecontact={quickCreateContact}
  oncreateprovider={quickCreateProvider}
  created={form?.inlineCreated ?? null}
/>
<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  pickerSlot={qcCompanySlot}
  definitions={data.companyDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>
<ContactQuickCreate
  bind:open={qcContactOpen}
  name={qcContactName}
  pickerSlot={qcContactSlot}
  definitions={data.contactDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>
<ProviderQuickCreate
  bind:open={qcProviderOpen}
  kind={qcProviderKind}
  name={qcProviderName}
  error={form?.qcError ?? null}
/>
<ConfirmDialog
  bind:open={confirmDelete}
  title={t("websites.delete")}
  message={t("websites.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>

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
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DomainQuickCreate from "$lib/modules/domains/DomainQuickCreate.svelte";
  import HostingQuickCreate from "$lib/modules/hosting/HostingQuickCreate.svelte";

  type Website = components["schemas"]["WebsiteRead"];

  let { data, form } = $props();

  // Deep link from the client page (?new=1&company=): the dialog opens with the domain
  // options narrowed to that client's domains.
  let showModal = $state(page.url.searchParams.has("new"));
  const initialCompanyId = page.url.searchParams.get("company") ?? "";
  let editing = $state<Website | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

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
  const hostingCreated = $derived(
    form?.inlineCreated?.slot === "hosting_account" ? form.inlineCreated.id : "",
  );
  // A domain created from the picker (#115): the refreshed load re-lists it as unclaimed, so the
  // Combobox resolves this id to its label and auto-selects it.
  const domainCreated = $derived(
    form?.inlineCreated?.slot === "domain" ? form.inlineCreated.id : "",
  );

  // Radio selection is component state, never a one-way checked (docs/UX.md).
  let hostChoice = $state<"root" | "www">("root");

  function openCreate() {
    editing = null;
    hostChoice = "root";
    showModal = true;
  }
  function openEdit(w: Website) {
    editing = w;
    hostChoice = w.root ? "root" : "www";
    showModal = true;
  }
  function requestDelete(id: string) {
    deleteId = id;
    confirmDelete = true;
  }
</script>

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

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.websites.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("websites.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.websites as site (site.id)}
        <li class="flex items-center gap-3 px-4 py-3">
          <a
            href={`/domains/${site.domain_id}#website`}
            class="min-w-0 flex-1 truncate text-sm font-medium text-text hover:text-brand"
          >
            {site.root ? site.domain_name : `www.${site.domain_name}`}
          </a>
          {#if site.company_name}
            <span class="hidden text-xs text-text-muted sm:inline">{site.company_name}</span>
          {/if}
          {#if site.hosting_name}
            <span class="hidden text-xs text-text-muted sm:inline">{site.hosting_name}</span>
          {/if}
          {#if site.uptime_enabled}
            <span
              class="rounded-full bg-green-500/10 px-2 py-0.5 text-[11px] text-green-700 dark:text-green-400"
            >
              {t("websites.uptime_short")}
            </span>
          {/if}
          <ActionsMenu
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(site) },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => requestDelete(site.id),
              },
            ]}
          />
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Modal bind:open={showModal} title={editing ? t("websites.edit") : t("websites.new")}>
  {#key editing?.id ?? "new"}
    <form
      method="POST"
      action="?/save"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") showModal = false;
          void update({ reset: false });
        }}
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
              value={domainCreated || undefined}
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
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{editing ? t("common.save") : t("websites.add")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

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

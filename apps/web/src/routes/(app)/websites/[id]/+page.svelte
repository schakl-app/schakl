<script lang="ts">
  /**
   * One website's detail page (the sibling of `/domains/[id]`).
   *
   * A website hangs off a domain and has no name of its own, so the heading is the host it
   * answers on and the domain sits right under it as a link — the two records are related, not
   * the same record, which is exactly what sharing a page used to imply.
   *
   * Use-vs-edit (docs/UX.md): the page reads as a record and the pencil turns it into a form.
   */
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { originOf, withOrigin } from "$lib/core/origin";
  import { can } from "$lib/core/permissions";
  import { entityPanelComponent } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import { companyLifecycle } from "$lib/modules/companies/picker";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import EditToggle from "$lib/core/ui/EditToggle.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import { pageTitle } from "$lib/core/title";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import HostingQuickCreate from "$lib/modules/hosting/HostingQuickCreate.svelte";

  let { data, form } = $props();

  let editing = $state(false);

  // A detour that started on a client's page (#408): every exit — Opslaan, Annuleren, ✕ and
  // Verwijderen — returns to where it started. With no `?from=` there is nowhere to return to and
  // each one behaves exactly as it did.
  const origin = $derived(originOf(page.url));
  function leaveEdit(): void {
    if (origin) void goto(origin, { invalidateAll: true });
    else editing = false;
  }
  let confirmDelete = $state(false);
  // Radio selection is component state, never a one-way checked (docs/UX.md).
  let host = $state<"root" | "www">("root");
  const busy = new InFlight();

  const website = $derived(data.website);
  // A website has no name of its own — the host it answers on is the name (§17's
  // `natural_keys=("domain",)` is the same fact, stated for spreadsheets).
  const title = $derived(website.root ? website.domain_name : `www.${website.domain_name}`);

  const canWrite = $derived(can(page.data.user, "websites.website.write"));
  const canDelete = $derived(can(page.data.user, "websites.website.delete"));

  // Inline-create from the pickers (#115): the slot names the picker that asked, so its
  // `inlineCreated` auto-selects only there.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcCompanySlot = $state("company");
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
  let qcContactSlot = $state("contact");
  let qcProviderOpen = $state(false);
  let qcProviderKind = $state<"registrar" | "dns" | "email" | "hosting">("hosting");
  let qcProviderName = $state("");
  let qcHostingOpen = $state(false);
  let qcHostingName = $state("");

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

  let hostingCreated = $state("");
  $effect(() => {
    const c = form?.inlineCreated;
    if (c?.slot === "hosting_account") hostingCreated = c.id;
  });
  const hostingItems = $derived(data.hosting.map((h) => ({ value: h.id, label: h.name })));

  function startEdit() {
    host = website.root ? "root" : "www";
    editing = true;
  }

  // The activity trail rides the core entity-panel seam (§16), like domain/project/contact.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  function panelComponent(key: string) {
    return entityPanelComponent(enabled, "website", key);
  }
  const emptyLookups = { members: [], companies: [], projects: [], tasks: [] };
</script>

<svelte:head>
  <title>{pageTitle(title)}</title>
</svelte:head>

<div class="mb-6">
  <a href="/websites" class="text-sm text-text-muted hover:text-brand">{t("websites.title")}</a>
  <div class="mt-2 flex items-center justify-between">
    <h1 class="text-xl font-semibold text-text">{title}</h1>
    <!-- Entering edit mode is a menu item, leaving it is a button (#337); the form keeps its own
         Opslaan/Annuleren at the bottom and the ⋯ no longer holds a third exit. -->
    {#if canWrite || canDelete}
      <EditToggle
        {editing}
        canEdit={canWrite}
        exit="cancel"
        onedit={startEdit}
        onexit={leaveEdit}
        items={canDelete
          ? [
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => (confirmDelete = true),
              },
            ]
          : []}
      />
    {/if}
  </div>
</div>

<section class="rounded-xl border border-border bg-surface-raised p-5">
  {#if !editing}
    <dl class="space-y-2 text-sm">
      <!-- The domain is a link, not a label: it is the other half of this pair, and the whole
           point of the split is that you can get from one to the other. -->
      <div class="flex justify-between gap-4">
        <dt class="text-text-muted">{t("websites.field.domain")}</dt>
        <dd class="truncate">
          <a href={`/domains/${website.domain_id}`} class="text-brand hover:underline">
            {website.domain_name}
          </a>
        </dd>
      </div>
      <div class="flex justify-between gap-4">
        <dt class="text-text-muted">{t("websites.company")}</dt>
        <dd class="truncate text-text">
          {#if website.company_id}
            <a href={`/companies/${website.company_id}`} class="text-brand hover:underline">
              {website.company_name}
            </a>
          {:else}—{/if}
        </dd>
      </div>
      <div class="flex justify-between gap-4">
        <dt class="text-text-muted">{t("websites.host")}</dt>
        <dd class="text-text">{website.root ? "@ (root)" : "www"}</dd>
      </div>
      <div class="flex justify-between gap-4">
        <dt class="text-text-muted">{t("websites.technical_owner")}</dt>
        <dd class="text-text">{website.technical_owner?.label || "—"}</dd>
      </div>
      <div class="flex justify-between gap-4">
        <dt class="text-text-muted">{t("websites.hosting")}</dt>
        <dd class="text-text">{website.hosting_name ?? "—"}</dd>
      </div>
      <div class="flex justify-between gap-4">
        <dt class="text-text-muted">{t("websites.uptime")}</dt>
        <dd class="text-text">{website.uptime_enabled ? t("common.yes") : t("common.no")}</dd>
      </div>
      <div class="flex justify-between gap-4">
        <dt class="text-text-muted">{t("table.column.created_at")}</dt>
        <dd class="text-text">{fmtDateTime(website.created_at)}</dd>
      </div>
    </dl>
    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-border pt-4">
        <CustomFieldsView
          definitions={data.definitions}
          values={website.custom ?? {}}
          locale={data.locale}
        />
      </div>
    {/if}
  {:else}
    <form
      method="POST"
      action="?/update"
      use:enhance={busy.wrap("save", () => async ({ result, update }) => {
        // Opened as a detour, saving ends it (#408); a refusal stays put with its message.
        if (result.type === "success" && origin) return void goto(origin, { invalidateAll: true });
        if (result.type === "success") editing = false;
        // Stay on the record after saving, so the fields keep what was just stored.
        void update({ reset: false });
      })}
    >
      <div class="space-y-4">
        <!-- The domain is not editable here: moving a site to another name is a different act
             (delete + create), and the unique index on (org, domain) is what enforces it. -->
        <div>
          <span class="mb-1 block text-sm text-text">{t("websites.field.domain")}</span>
          <p class="text-sm text-text-muted">{website.domain_name}</p>
        </div>
        <div>
          <span class="mb-1 block text-sm text-text">{t("websites.host")}</span>
          <div class="flex gap-3">
            <label class="flex items-center gap-1.5 text-sm text-text">
              <input type="radio" name="root" value="root" bind:group={host} /> @ (root)
            </label>
            <label class="flex items-center gap-1.5 text-sm text-text">
              <input type="radio" name="root" value="www" bind:group={host} />
              www
            </label>
          </div>
        </div>
        <div>
          <span class="mb-1 block text-sm text-text">{t("websites.technical_owner")}</span>
          <PartyPicker
            name="technical_owner"
            value={website.technical_owner ?? { type: "agency", id: null }}
            agencyLabel={data.agencyLabel}
            companies={data.companies}
            companyLifecycle={companyLifecycle()}
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
            value={hostingCreated || (website.hosting_id ?? "")}
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
            checked={website.uptime_enabled}
          />
          {t("websites.uptime")}
        </label>
        {#if data.definitions.length > 0}
          <CustomFieldsForm
            definitions={data.definitions}
            values={website.custom ?? {}}
            locale={data.locale}
          />
        {:else}
          <input type="hidden" name="custom" value={JSON.stringify(website.custom ?? {})} />
        {/if}
      </div>
      {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">
          {t(form.error)}
        </p>{/if}
      <div class="mt-4 flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={leaveEdit}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("save")} disabled={busy.active}>{t("common.save")}</Button>
      </div>
    </form>
  {/if}
</section>

<!-- Core entity panels (the activity trail, §16) — history hangs last, under the work. -->
{#each data.panels as panel (panel.key)}
  {@const PanelComponent = panelComponent(panel.key)}
  {#if PanelComponent}
    <section class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="mb-3 text-sm font-semibold text-text">{t(panel.titleKey)}</h2>
      <PanelComponent data={panel.data} context={data.context} lookups={emptyLookups} />
    </section>
  {/if}
{/each}

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
  initialCompanyId={website.company_id ?? ""}
  error={form?.qcError ?? null}
  oncreatecompany={quickCreateCompany}
  oncreatecontact={quickCreateContact}
  oncreateprovider={(kind, name) => {
    qcProviderKind = kind;
    qcProviderName = name;
    qcProviderOpen = true;
  }}
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
  action={withOrigin("?/delete", page.url)}
/>

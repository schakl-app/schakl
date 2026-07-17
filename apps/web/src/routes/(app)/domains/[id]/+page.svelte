<script lang="ts">
  import { RefreshCw, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DomainForm from "$lib/modules/domains/DomainForm.svelte";
  import HostingQuickCreate from "$lib/modules/hosting/HostingQuickCreate.svelte";

  let { data, form } = $props();

  let editing = $state(false);
  let editingWebsite = $state(false);
  let confirmDelete = $state(false);

  // Inline-create from the pickers (#115): "＋ … toevoegen" opens these dialogs.
  // The slot names the picker that asked, so its `inlineCreated` auto-selects only there.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcCompanySlot = $state("company");
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
  let qcContactSlot = $state("contact");
  let qcProviderOpen = $state(false);
  let qcProviderKind = $state<"registrar" | "dns" | "email">("registrar");
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

  // The website form's hosting picker lives in this page, so its auto-select does too.
  let websiteHostingCreated = $state("");
  $effect(() => {
    const c = form?.inlineCreated;
    if (c?.slot === "hosting_account") websiteHostingCreated = c.id;
  });

  const domain = $derived(data.domain);
  const website = $derived(data.website);
  const hostingItems = $derived(data.hosting.map((h) => ({ value: h.id, label: h.name })));

  // Through the shared formatter (#125): tenant timezone + the personal clock/date prefs,
  // instead of the browser-locale toLocaleString dump this replaced.
  function checkedAt(iso: string | null | undefined): string {
    return iso ? fmtDateTime(iso) : t("domains.dns.never");
  }
</script>

<svelte:head>
  <title>{domain.name}</title>
</svelte:head>

<div class="mb-6">
  <a href="/domains" class="text-sm text-text-muted hover:text-text">← {t("domains.title")}</a>
  <div class="mt-2 flex items-center justify-between">
    <h1 class="text-xl font-semibold text-text">{domain.name}</h1>
    <ActionsMenu
      items={[
        {
          label: editing ? t("common.cancel") : t("common.edit"),
          onclick: () => (editing = !editing),
        },
        {
          label: t("common.delete"),
          icon: Trash2,
          danger: true,
          onclick: () => (confirmDelete = true),
        },
      ]}
    />
  </div>
</div>

<div class="grid gap-4 lg:grid-cols-2">
  <!-- Details -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-4 text-sm font-semibold text-text">{t("domains.details")}</h2>
    {#if editing}
      <form
        method="POST"
        action="?/update"
        use:enhance={() =>
          ({ result, update }) => {
            if (result.type === "success") editing = false;
            void update({ reset: false });
          }}
      >
        <DomainForm
          {domain}
          companies={data.companies}
          providers={data.providers}
          employees={data.employees}
          contacts={data.contacts}
          agencyLabel={data.agencyLabel}
          definitions={data.definitions}
          locale={data.locale}
          idPrefix="edit-domain"
          oncreatecompany={quickCreateCompany}
          oncreatecontact={quickCreateContact}
          oncreateprovider={(kind, name) => {
            qcProviderKind = kind;
            qcProviderName = name;
            qcProviderOpen = true;
          }}
          created={form?.inlineCreated ?? null}
        />
        {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">
            {t(form.error)}
          </p>{/if}
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm text-text"
            onclick={() => (editing = false)}>{t("common.cancel")}</button
          >
          <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
            >{t("common.save")}</button
          >
        </div>
      </form>
    {:else}
      <dl class="space-y-2 text-sm">
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.company")}</dt>
          <dd class="text-text">{domain.company_name}</dd>
        </div>
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.status")}</dt>
          <dd class="text-text">{t(`domains.status.${domain.status}`)}</dd>
        </div>
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.registrar")}</dt>
          <dd class="text-text">{domain.registrar_provider_name ?? "—"}</dd>
        </div>
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.dns")}</dt>
          <dd class="text-text">{domain.dns_provider_name ?? "—"}</dd>
        </div>
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.registry_contact")}</dt>
          <dd class="text-text">{domain.registry_contact?.label || "—"}</dd>
        </div>
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.email_enabled")}</dt>
          <dd class="text-text">
            {domain.email_enabled ? t("common.yes") : t("common.no")}
            {#if domain.email_enabled && domain.email_provider_name}
              — {domain.email_provider_name}{/if}
          </dd>
        </div>
      </dl>
    {/if}
  </section>

  <!-- DNS -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <div class="mb-4 flex items-center justify-between">
      <h2 class="text-sm font-semibold text-text">{t("domains.dns.title")}</h2>
      <form method="POST" action="?/refresh" use:enhance>
        <button
          class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand"
        >
          <RefreshCw size={14} />{t("domains.dns.refresh")}
        </button>
      </form>
    </div>
    <dl class="space-y-2 text-sm">
      <div>
        <dt class="text-text-muted">{t("domains.dns.nameservers")}</dt>
        <dd class="mt-1 text-text">
          {#if domain.nameservers && domain.nameservers.length > 0}
            <ul class="space-y-0.5 font-mono text-xs">
              {#each domain.nameservers as ns (ns)}<li>{ns}</li>{/each}
            </ul>
          {:else}—{/if}
        </dd>
      </div>
      <div>
        <dt class="text-text-muted">{t("domains.dns.mx")}</dt>
        <dd class="mt-1 text-text">
          {#if domain.mx_records && domain.mx_records.length > 0}
            <ul class="space-y-0.5 font-mono text-xs">
              {#each domain.mx_records as mx (`${mx.priority}-${mx.exchange}`)}
                <li><span class="text-text-muted">{mx.priority}</span> {mx.exchange}</li>
              {/each}
            </ul>
          {:else}—{/if}
        </dd>
      </div>
      <div class="flex justify-between">
        <dt class="text-text-muted">{t("domains.dns.dnssec")}</dt>
        <dd class="text-text">
          {domain.dnssec === null
            ? t("domains.dns.unknown")
            : domain.dnssec
              ? t("common.yes")
              : t("common.no")}
        </dd>
      </div>
      <div class="flex justify-between">
        <dt class="text-text-muted">{t("domains.dns.checked_at")}</dt>
        <dd class="text-text">{checkedAt(domain.dns_checked_at)}</dd>
      </div>
    </dl>
  </section>
</div>

<!-- Website (0/1 per domain). The `id` anchors the client page's "＋ website" quick link. -->
<section id="website" class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-4 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-text">{t("websites.title")}</h2>
    {#if website && !editingWebsite}
      <ActionsMenu
        items={[
          { label: t("common.edit"), onclick: () => (editingWebsite = true) },
          {
            label: t("common.delete"),
            icon: Trash2,
            danger: true,
            onclick: () =>
              (
                document.getElementById("delete-website-form") as HTMLFormElement | null
              )?.requestSubmit(),
          },
        ]}
      />
    {/if}
  </div>

  {#if website && !editingWebsite}
    <dl class="space-y-2 text-sm">
      <div class="flex justify-between">
        <dt class="text-text-muted">{t("websites.host")}</dt>
        <dd class="text-text">{website.root ? "@ (root)" : "www"}</dd>
      </div>
      <div class="flex justify-between">
        <dt class="text-text-muted">{t("websites.technical_owner")}</dt>
        <dd class="text-text">{website.technical_owner?.label || "—"}</dd>
      </div>
      <div class="flex justify-between">
        <dt class="text-text-muted">{t("websites.hosting")}</dt>
        <dd class="text-text">{website.hosting_name ?? "—"}</dd>
      </div>
      <div class="flex justify-between">
        <dt class="text-text-muted">{t("websites.uptime")}</dt>
        <dd class="text-text">{website.uptime_enabled ? t("common.yes") : t("common.no")}</dd>
      </div>
    </dl>
    <form
      id="delete-website-form"
      method="POST"
      action="?/deleteWebsite"
      use:enhance
      class="hidden"
    >
      <input type="hidden" name="website_id" value={website.id} />
    </form>
  {:else if editingWebsite || !website}
    <form
      method="POST"
      action="?/saveWebsite"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") editingWebsite = false;
          void update({ reset: false });
        }}
    >
      {#if website}<input type="hidden" name="website_id" value={website.id} />{/if}
      <div class="space-y-4">
        <div>
          <span class="mb-1 block text-sm text-text">{t("websites.host")}</span>
          <div class="flex gap-3">
            <label class="flex items-center gap-1.5 text-sm text-text">
              <input type="radio" name="root" value="root" checked={website?.root ?? true} /> @ (root)
            </label>
            <label class="flex items-center gap-1.5 text-sm text-text">
              <input
                type="radio"
                name="root"
                value="www"
                checked={website ? !website.root : false}
              />
              www
            </label>
          </div>
        </div>
        <div>
          <span class="mb-1 block text-sm text-text">{t("websites.technical_owner")}</span>
          <PartyPicker
            name="technical_owner"
            value={website?.technical_owner ?? { type: "agency", id: null }}
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
            value={websiteHostingCreated || (website?.hosting_id ?? "")}
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
            checked={website?.uptime_enabled ?? false}
          />
          {t("websites.uptime")}
        </label>
        {#if data.websiteDefinitions.length > 0}
          <CustomFieldsForm
            definitions={data.websiteDefinitions}
            values={website?.custom ?? {}}
            locale={data.locale}
          />
        {:else}
          <input type="hidden" name="custom" value={JSON.stringify(website?.custom ?? {})} />
        {/if}
      </div>
      {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">
          {t(form.error)}
        </p>{/if}
      <div class="mt-4 flex justify-end gap-2">
        {#if website}
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm text-text"
            onclick={() => (editingWebsite = false)}>{t("common.cancel")}</button
          >
        {/if}
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{website ? t("common.save") : t("websites.add")}</button
        >
      </div>
    </form>
  {/if}
</section>

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
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("domains.delete")}
  message={t("domains.delete_confirm")}
  action="?/delete"
/>

<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { editIntent } from "$lib/core/edit-intent";
  import { t } from "$lib/core/i18n";
  import { formatPhone } from "$lib/core/phone";
  import { can } from "$lib/core/permissions";
  import { entityPanelsFor } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import LinkField from "$lib/core/ui/LinkField.svelte";
  import Markdown from "$lib/core/ui/Markdown.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import PhoneInput from "$lib/core/ui/PhoneInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";

  let { data, form } = $props();

  // Opened straight into edit when reached from the overview's ⋯ → Bewerken (#78).
  let editing = $state(editIntent());

  // Header actions render only for holders of the matching permission (#253).
  const canWrite = $derived(can(page.data.user, "contacts.contact.write"));
  const canDelete = $derived(can(page.data.user, "contacts.contact.delete"));
  let confirmDelete = $state(false);
  const contact = $derived(data.contact);
  const custom = $derived((contact.custom ?? {}) as Record<string, unknown>);
  const fullName = $derived([contact.first_name, contact.last_name].filter(Boolean).join(" "));

  // Panels contributed to a contact page (CLAUDE.md §6) — the core activity trail today (#67).
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const panelSpecs = $derived(entityPanelsFor(enabled, "contact"));
  const panelComponent = (key: string) => panelSpecs.find((spec) => spec.key === key)?.component;
  // The activity panel reads no lookups; hand it the id/name shapes the page already holds.
  const panelLookups = $derived({
    members: data.members,
    companies: data.companies.map((c) => ({ id: c.id, name: c.name })),
    projects: [],
    tasks: [],
  });
  const companyDefinitions = $derived((data.companyDefinitions ?? []) as CustomFieldDefinition[]);

  // --- linked clients (many-to-many) ----------------------------------------
  interface CompanyLink {
    company_id: string;
    name: string;
    is_primary: boolean;
  }
  const companyLinks = $derived((contact.companies ?? []) as CompanyLink[]);
  const links = $derived(
    companyLinks.map((c) => ({ id: c.company_id, label: c.name, is_primary: c.is_primary })),
  );
  const linkedIds = $derived(new Set(companyLinks.map((c) => c.company_id)));
  // The primary client scopes the notes editor's @/# candidates (#237): that company's
  // contacts and tasks, the same host-link rule the task page applies.
  const primaryCompanyId = $derived(
    (companyLinks.find((c) => c.is_primary) ?? companyLinks[0])?.company_id ?? null,
  );
  const candidateCompanies = $derived(
    data.companies.filter((c) => !linkedIds.has(c.id)).map((c) => ({ value: c.id, label: c.name })),
  );

  let showCreateCompany = $state(false);
  let draftCompanyName = $state("");
  function openCreateCompany(query: string) {
    draftCompanyName = query.trim();
    showCreateCompany = true;
  }

  // Submits in flight (#242): the firing button spins, its siblings freeze — the portal's
  // enable/resend/disable all mutate the same portal, so only one may run at a time.
  const busy = new InFlight();
</script>

<svelte:head>
  <title>{pageTitle(fullName)}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="mt-2 text-xl font-semibold text-text">{fullName}</h1>
  </div>
  {#if canWrite || canDelete}
    <ActionsMenu
      items={[
        ...(canWrite
          ? [
              {
                label: editing ? t("common.cancel") : t("common.edit"),
                icon: Pencil,
                onclick: () => (editing = !editing),
              },
            ]
          : []),
        ...(canDelete
          ? [
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => (confirmDelete = true),
              },
            ]
          : []),
      ]}
    />
  {/if}
</div>

<!-- Linked clients: chips navigate in use mode; attaching, detaching and promoting appear only
     under the header's ⋯ → Bewerken, like every other definition change (docs/UX.md §3). -->
<section class="mb-4 rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-4 text-sm font-semibold text-text">{t("contacts.companies")}</h2>
  <LinkField
    {links}
    {editing}
    candidates={candidateCompanies}
    idField="company_id"
    linkAction="?/linkCompany"
    unlinkAction="?/unlinkCompany"
    primaryAction="?/setPrimaryCompany"
    id="contact-company-picker"
    placeholder={t("contacts.add_client")}
    chipHref={(cid) => `/companies/${cid}`}
    labels={{
      primary: t("contacts.primary"),
      makePrimary: t("contacts.make_primary"),
      remove: t("contacts.unlink"),
    }}
    oncreate={openCreateCompany}
  />
</section>

{#if editing && canWrite}
  <form
    method="POST"
    action="?/update"
    use:enhance={busy.wrap("save", () => ({ update }) => {
      void update().then(() => (editing = false));
    })}
    class="rounded-xl border border-border bg-surface-raised p-5"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="first_name" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.first_name")}</label
        >
        <input
          id="first_name"
          name="first_name"
          required
          value={contact.first_name}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="last_name" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.last_name")}</label
        >
        <input
          id="last_name"
          name="last_name"
          value={contact.last_name ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.email")}</label
        >
        <input
          id="email"
          name="email"
          type="email"
          value={contact.email ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="phone" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.phone")}</label
        >
        <PhoneInput id="phone" name="phone" value={contact.phone ?? ""} />
      </div>
      <div class="sm:col-span-2">
        <label for="job_title" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.job_title")}</label
        >
        <input
          id="job_title"
          name="job_title"
          value={contact.job_title ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div class="sm:col-span-2">
        <label for="contact-notes" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.notes")}</label
        >
        <RichTextEditor
          id="contact-notes"
          name="notes"
          rows={3}
          value={contact.notes ?? ""}
          scope={{ companyId: primaryCompanyId }}
        />
      </div>
    </div>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-border pt-4">
        <CustomFieldsForm
          definitions={data.definitions}
          values={custom}
          locale={data.locale}
          scope={{ companyId: primaryCompanyId }}
        />
      </div>
    {/if}

    {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="mt-4 flex gap-2">
      <Button loading={busy.is("save")}>{t("common.save")}</Button>
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (editing = false)}>{t("common.cancel")}</button
      >
    </div>
  </form>
{:else}
  <div class="grid gap-4">
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <dl class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("contacts.email")}
          </dt>
          <dd class="mt-1 text-sm text-text">{contact.email ?? "—"}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("contacts.phone")}
          </dt>
          <dd class="mt-1 text-sm text-text">
            {#if contact.phone}
              <a href="tel:{contact.phone}" class="hover:text-brand">{formatPhone(contact.phone)}</a
              >
            {:else}—{/if}
          </dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("contacts.job_title")}
          </dt>
          <dd class="mt-1 text-sm text-text">{contact.job_title ?? "—"}</dd>
        </div>
        {#if contact.notes}
          <div class="sm:col-span-2">
            <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
              {t("contacts.notes")}
            </dt>
            <dd class="mt-1">
              <Markdown value={contact.notes} />
            </dd>
          </div>
        {/if}
      </dl>
    </section>

    {#if data.definitions.length > 0}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <h2 class="mb-4 text-sm font-semibold text-text">{t("contacts.panel.custom")}</h2>
        <CustomFieldsView definitions={data.definitions} values={custom} locale={data.locale} />
      </section>
    {/if}

    {#if data.canPortal && data.portal}
      <!-- Client portal (#193): give this contact a login that lands on their companies'
           curated dashboards. Enable/disable is reversible; the API is the boundary. -->
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <h2 class="text-sm font-semibold text-text">{t("contacts.portal.title")}</h2>
          <span
            class="rounded-full px-2 py-0.5 text-[11px] font-medium
              {data.portal.status === 'active'
              ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
              : data.portal.status === 'invited'
                ? 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
                : data.portal.status === 'disabled'
                  ? 'bg-surface text-text-muted ring-1 ring-inset ring-border'
                  : 'text-text-muted ring-1 ring-inset ring-border'}"
          >
            {t(`contacts.portal.status.${data.portal.status}`)}
          </span>
        </div>
        <p class="mb-3 text-sm text-text-muted">{t("contacts.portal.hint")}</p>
        {#if form?.portalError}
          <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(form.portalError)}</p>
        {/if}
        {#if form?.portalSaved && form?.portalEmail === false}
          <p class="mb-3 text-sm text-amber-700 dark:text-amber-400">
            {t("contacts.portal.email_not_sent")}
          </p>
        {/if}
        <div class="flex flex-wrap gap-2">
          {#if data.portal.status === "none" || data.portal.status === "disabled"}
            <form method="POST" action="?/portalEnable" use:enhance={busy.wrap("enable")}>
              <Button size="sm" loading={busy.is("enable")} disabled={busy.active}>
                {data.portal.status === "disabled"
                  ? t("contacts.portal.reenable")
                  : t("contacts.portal.enable")}
              </Button>
            </form>
          {:else}
            {#if data.portal.status === "invited"}
              <form method="POST" action="?/portalResend" use:enhance={busy.wrap("resend")}>
                <Button
                  variant="secondary"
                  size="sm"
                  loading={busy.is("resend")}
                  disabled={busy.active}
                >
                  {t("contacts.portal.resend")}
                </Button>
              </form>
            {/if}
            <form method="POST" action="?/portalDisable" use:enhance={busy.wrap("disable")}>
              <Button
                variant="danger-outline"
                size="sm"
                loading={busy.is("disable")}
                disabled={busy.active}
              >
                {t("contacts.portal.disable")}
              </Button>
            </form>
          {/if}
        </div>
      </section>
    {/if}
  </div>
{/if}

<!-- Panels contributed by the registry — the activity trail hangs last (history under the
     working surfaces). Every auditable record carries one (docs/UX.md principle 4, #67). -->
{#each data.panels as panel (panel.key)}
  {@const PanelComponent = panelComponent(panel.key)}
  {#if PanelComponent}
    <section class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="mb-3 text-sm font-semibold text-text">{t(panel.titleKey)}</h2>
      <PanelComponent data={panel.data} context={data.context} lookups={panelLookups} />
    </section>
  {/if}
{/each}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("contacts.delete_confirm", { name: fullName })}
  action="?/delete"
/>

<!-- Quick-create a new client and attach it to this contact, without leaving the page. -->
<Modal bind:open={showCreateCompany} title={t("contacts.new_client")}>
  {#key draftCompanyName + String(showCreateCompany)}
    <form
      method="POST"
      action="?/createCompany"
      use:enhance={busy.wrap("createCompany", () => ({ update }) => {
        showCreateCompany = false;
        void update();
      })}
      class="space-y-3"
    >
      <!-- The full client form, prefilled with what was typed — never a name-only stub. -->
      <CompanyForm
        company={{ name: draftCompanyName }}
        members={data.members}
        definitions={companyDefinitions}
        locale={data.locale}
        idPrefix="quick-company"
      />
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2 pt-1">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showCreateCompany = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("createCompany")}>{t("common.create")}</Button>
      </div>
    </form>
  {/key}
</Modal>

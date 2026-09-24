<script lang="ts">
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import Trash2 from "@lucide/svelte/icons/trash-2";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtDateTime, fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t, tn } from "$lib/core/i18n";
  import { fromHref, originOf, withOrigin } from "$lib/core/origin";
  import { can } from "$lib/core/permissions";
  import { entityPanelComponent } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import EditToggle from "$lib/core/ui/EditToggle.svelte";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";
  import ProviderQuickCreate from "$lib/core/ui/ProviderQuickCreate.svelte";
  import { pageTitle } from "$lib/core/title";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DomainForm from "$lib/modules/domains/DomainForm.svelte";

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
  const busy = new InFlight();

  // Inline-create from the pickers (#115): "＋ … toevoegen" opens these dialogs.
  // The slot names the picker that asked, so its `inlineCreated` auto-selects only there.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcCompanySlot = $state("company");
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
  let qcContactSlot = $state("contact");
  let qcProviderOpen = $state(false);
  let qcProviderKind = $state<"registrar" | "dns" | "email" | "hosting">("registrar");
  let qcProviderName = $state("");

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

  const domain = $derived(data.domain);
  // The sites on this domain, in address order (`sort=name`): root, then by path.
  const websites = $derived(data.websites);
  // Defaulted server-side (#298), so the schema types it optional; an absent list and an empty
  // one mean the same thing here — no register holds this name.
  const registers = $derived(domain.registers ?? []);

  // Actions render only for holders of the matching permission (#253). The DNS refresh posts
  // a write on the API, so it follows domains.domain.write.
  const canWrite = $derived(can(page.data.user, "domains.domain.write"));
  const canDelete = $derived(can(page.data.user, "domains.domain.delete"));
  const canWriteWebsite = $derived(can(page.data.user, "websites.website.write"));

  // Through the shared formatter (#125): tenant timezone + the personal clock/date prefs,
  // instead of the browser-locale toLocaleString dump this replaced.
  function checkedAt(iso: string | null | undefined): string {
    return iso ? fmtDateTime(iso) : t("domains.dns.never");
  }

  // The activity trail rides the core entity-panel seam (§16), like project/contact.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  function panelComponent(key: string) {
    return entityPanelComponent(enabled, "domain", key);
  }
  const emptyLookups = { members: [], companies: [], projects: [], tasks: [] };
</script>

<svelte:head>
  <title>{pageTitle(domain.name)}</title>
</svelte:head>

<div class="mb-6">
  <div class="mt-2 flex items-center justify-between">
    <h1 class="text-xl font-semibold text-text">{domain.name}</h1>
    <!-- Entering edit mode is a menu item, leaving it is a button (#337); the form keeps its own
         Opslaan/Annuleren at the bottom and the ⋯ no longer holds a third exit. -->
    {#if canWrite || canDelete}
      <EditToggle
        {editing}
        canEdit={canWrite}
        exit="cancel"
        onedit={() => (editing = true)}
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

<div class="grid gap-4 lg:grid-cols-2">
  <!-- Details -->
  <section class="rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-4 text-sm font-semibold text-text">{t("domains.details")}</h2>
    {#if editing}
      <form
        method="POST"
        action="?/update"
        use:enhance={busy.wrap("update", () => async ({ result, update }) => {
          // The detour is over the moment the write lands; a refusal stays put with its message.
          if (result.type === "success" && origin)
            return void goto(origin, { invalidateAll: true });
          if (result.type === "success") editing = false;
          void update({ reset: false });
        })}
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
          tldPrices={data.tldPrices}
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
            onclick={leaveEdit}>{t("common.cancel")}</button
          >
          <Button loading={busy.is("update")} disabled={busy.active}>{t("common.save")}</Button>
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
        {#if domain.status === "redirect"}
          <div class="flex justify-between gap-4">
            <dt class="text-text-muted">{t("domains.redirect_url")}</dt>
            <dd class="min-w-0 truncate text-text">
              {#if domain.redirect_url}
                <a
                  href={domain.redirect_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  class="text-brand hover:underline">{domain.redirect_url}</a
                >
              {:else}—{/if}
            </dd>
          </div>
        {/if}
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.start_date")}</dt>
          <dd class="text-text">{fmtNumericDate(domain.start_date)}</dd>
        </div>
        <div class="flex justify-between gap-3">
          <dt class="text-text-muted">{t("domains.renewal")}</dt>
          <dd class="text-right text-text">
            {domain.next_invoice_date ? fmtNumericDate(domain.next_invoice_date) : "—"}
            <!-- What the registrar last observed, when it is not what we bill on. Reported and
                 never applied (CLAUDE.md §10): "somebody changed this in the provider's
                 dashboard" has to be expressible, and a mirror that overwrites cannot say it.
                 The edit form is where the two are reconciled, in one click. -->
            {#if domain.register_expires_on && domain.register_expires_on !== domain.next_invoice_date}
              <span class="block text-xs text-text-muted">
                {t("domains.register_expiry_differs", {
                  date: fmtNumericDate(domain.register_expires_on),
                })}
              </span>
            {/if}
          </dd>
        </div>
        {#if domain.billed_until}
          <!-- The operator's "already invoiced up to" statement (a migrated portfolio): drawn
               only when made, since NULL says nothing. -->
          <div class="flex justify-between gap-3">
            <dt class="text-text-muted">{t("domains.billed_until")}</dt>
            <dd class="text-right text-text">{fmtNumericDate(domain.billed_until)}</dd>
          </div>
        {/if}
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("domains.price")}</dt>
          <dd class="text-text">
            {#if domain.resolved_price != null}
              {t("domains.price_per_year", { amount: fmtMoney(Number(domain.resolved_price)) })}
              <span class="text-xs text-text-muted">
                ({domain.price_override != null
                  ? t("domains.price_source_override")
                  : t("domains.price_source_tld")})
              </span>
            {:else}
              {t("domains.no_price")}
            {/if}
          </dd>
        </div>
        <!-- Whether the renewal is billed on (#298), with the rule that decided it: a "no"
             nobody can explain is the one an agency notices a year late. The agency's decision,
             so the agency's row (the list's column is staff-only for the same reason). -->
        {#if !page.data.user?.isPortal}
          <div class="flex justify-between gap-3">
            <dt class="text-text-muted">{t("domains.invoiceable.legend")}</dt>
            <dd class="text-right text-text">
              {domain.invoiceable_effective
                ? t("domains.invoiceable.yes")
                : t("domains.invoiceable.no")}
              <span class="block text-xs text-text-muted">
                {#if domain.invoiceable != null}
                  {t("domains.invoiceable.source_explicit")}
                {:else if registers.length > 0}
                  {t("domains.invoiceable.follow_hint_held", {
                    register: registers.map((key) => t(`domains.register.${key}`)).join(", "),
                  })}
                {:else if domain.invoiceable_source === "register"}
                  {t("domains.invoiceable.follow_hint_absent")}
                {:else}
                  {t("domains.invoiceable.follow_hint_none")}
                {/if}
              </span>
            </dd>
          </div>
        {/if}
        {#if !page.data.user?.isPortal}
          <!-- The register we renew at is our supplier, not the client's business — the same
               audience rule the list's registrar column follows. -->
          <div class="flex justify-between">
            <dt class="text-text-muted">{t("domains.registrar")}</dt>
            <dd class="text-text">{domain.registrar_provider_name ?? "—"}</dd>
          </div>
        {/if}
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
      {#if canWrite}
        <form method="POST" action="?/refresh" use:enhance={busy.wrap("refresh")}>
          <Button variant="secondary" size="sm" loading={busy.is("refresh")} disabled={busy.active}>
            <RefreshCw size={14} />{t("domains.dns.refresh")}
          </Button>
        </form>
      {/if}
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

<!-- Websites on this domain — one per address (`app/core/webaddress.py`), each with its own
     page, so the section lists rather than holds: a domain of the agency's may carry a dev
     install per client. Creating one is the websites dialog opened on this domain. The `id`
     anchors the client page's "＋ website" quick link. -->
<section id="website" class="mt-4 rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-4 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-text">{t("websites.panel.title")}</h2>
    {#if canWriteWebsite}
      <a href={`/websites?domain=${domain.id}&new=1`} class="text-sm text-brand hover:underline">
        ＋ {t("websites.new")}
      </a>
    {/if}
  </div>
  {#if websites.length === 0}
    <p class="text-sm text-text-muted">{t("websites.panel.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each websites as site (site.id)}
        <!-- The address as the API resolves it; the client where the site names one of its own
             (a dev install on the agency's domain), which is the one fact the domain cannot say. -->
        <PanelRow
          href={fromHref(`/websites/${site.id}`, page.url)}
          title={site.label}
          meta={site.company_override_id ? site.company_name : null}
        />
      {/each}
    </ul>
    {#if data.websiteTotal > websites.length}
      <a
        href={`/websites?q=${encodeURIComponent(domain.name)}`}
        class="mt-3 inline-block text-sm text-brand hover:underline"
      >
        {tn("websites.panel.view_all", data.websiteTotal)}
      </a>
    {/if}
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
  title={t("domains.delete")}
  message={t("domains.delete_confirm")}
  action={withOrigin("?/delete", page.url)}
/>

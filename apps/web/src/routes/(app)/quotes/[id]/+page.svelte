<script lang="ts">
  import { Download, Check, FileText, Pencil, Printer, Trash2, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { editIntent } from "$lib/core/edit-intent";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { entityPanelsFor } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DocumentForm from "$lib/modules/invoicing/DocumentForm.svelte";
  import DocumentView from "$lib/modules/invoicing/DocumentView.svelte";

  let { data, form } = $props();

  const quote = $derived(data.quote);
  const isDraft = $derived(quote.status === "draft");
  let editing = $state(editIntent());
  $effect(() => {
    if (form?.saved || form?.issued) editing = false;
  });

  const busy = new InFlight();
  let confirmIssue = $state(false);
  let confirmDelete = $state(false);
  let confirmConvert = $state(false);
  let sendOpen = $state(false);
  let decisionOpen = $state(false);
  let decisionAction = $state<"accept" | "reject">("accept");

  // Inline-create from the edit form's contact picker (#115): "＋ … toevoegen" opens this dialog.
  let qcContactOpen = $state(false);
  let qcContactName = $state("");

  const template = $derived(data.templates.find((tpl) => tpl.id === quote.template_id) ?? null);
  const theme = $derived(page.data.theme);

  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const panelSpecs = $derived(entityPanelsFor(enabled, "quote"));
  function panelComponent(key: string) {
    return panelSpecs.find((spec) => spec.key === key)?.component;
  }
  const emptyLookups = { members: [], companies: [], projects: [], tasks: [] };

  function openDecision(action: "accept" | "reject") {
    decisionAction = action;
    decisionOpen = true;
  }

  const title = $derived(quote.number ?? t("invoicing.quote_status.draft"));
  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(`${t("invoicing.quotes")} ${title}`)}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <div class="flex min-w-0 items-center gap-3">
    <h1 class="truncate text-xl font-semibold text-text">{t("invoicing.quotes")} {title}</h1>
    <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
      >{t(`invoicing.quote_status.${quote.status}`)}</span
    >
    <a
      href="/companies/{quote.company_id}"
      class="truncate text-sm text-text-muted hover:text-brand">{quote.company_name}</a
    >
  </div>
  <div class="flex items-center gap-2">
    {#if isDraft && data.canWrite}
      <button
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        onclick={() => (confirmIssue = true)}>{t("invoicing.action.issue")}</button
      >
    {/if}
    {#if quote.status === "open"}
      {#if data.canSend}
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          onclick={() => (sendOpen = true)}>{t("invoicing.action.send")}</button
        >
      {/if}
      {#if data.canWrite}
        <button
          class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand"
          onclick={() => openDecision("accept")}
        >
          <Check size={14} class="mr-1 inline" />{t("invoicing.action.accept")}
        </button>
        <button
          class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-red-400"
          onclick={() => openDecision("reject")}
        >
          <X size={14} class="mr-1 inline" />{t("invoicing.action.reject")}
        </button>
      {/if}
    {/if}
    {#if quote.status === "accepted" && data.canInvoice}
      <button
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        onclick={() => (confirmConvert = true)}>{t("invoicing.action.convert")}</button
      >
    {/if}
    {#if quote.status === "invoiced" && quote.invoice_id}
      <a
        href="/invoices/{quote.invoice_id}"
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand"
        >{t("invoicing.action.view_invoice")}</a
      >
    {/if}
    <ActionsMenu
      items={[
        ...(data.canWrite && (isDraft || quote.status === "open")
          ? [{ label: t("common.edit"), icon: Pencil, onclick: () => (editing = !editing) }]
          : []),
        ...(!isDraft
          ? [
              {
                label: t("invoicing.action.download_pdf"),
                icon: Download,
                href: `/quotes/${quote.id}/pdf`,
              },
              {
                label: t("invoicing.action.print"),
                icon: Printer,
                href: `/quotes/${quote.id}/print`,
              },
            ]
          : []),
        ...(quote.status === "expired" && data.canWrite
          ? [
              {
                label: t("invoicing.action.accept"),
                icon: Check,
                onclick: () => openDecision("accept"),
              },
            ]
          : []),
        ...(["draft", "rejected", "expired"].includes(quote.status) && data.canDelete
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
  </div>
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}
{#if quote.sent_at}
  <p class="mb-4 text-xs text-text-muted">
    {t("invoicing.sent_at", { date: fmtDateTime(quote.sent_at) })}
  </p>
{/if}
{#if quote.decision_note}
  <p class="mb-4 rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-text">
    <FileText size={14} class="mr-1 inline text-text-muted" />
    {quote.decision_note}
  </p>
{/if}

<div class="space-y-6">
  {#if editing}
    <div class="max-w-4xl rounded-xl border border-border bg-surface-raised p-6">
      <DocumentForm
        kind="quote"
        doc={quote}
        action="?/save"
        contacts={data.contacts}
        taxRates={data.taxRates}
        products={data.products}
        templates={data.templates}
        settings={data.settings}
        locale={data.locale}
        {form}
        oncancel={() => (editing = false)}
        oncreatecontact={(name) => {
          qcContactName = name;
          qcContactOpen = true;
        }}
      />
      <input type="hidden" name="_status" value={quote.status} form="doc-form-quote" />
    </div>
  {:else}
    <DocumentView
      doc={quote}
      kind="quote"
      {template}
      seller={data.settings?.company_details ?? {}}
      brandName={theme?.brandName ?? ""}
      logoUrl={theme?.logoUrl ?? null}
      brandColor={theme?.primaryColor ?? "#4f46e5"}
    />
  {/if}

  {#each data.panels as panel (panel.key)}
    {@const PanelComponent = panelComponent(panel.key)}
    {#if PanelComponent}
      <section
        class="mx-auto w-full max-w-3xl rounded-xl border border-border bg-surface-raised p-4"
      >
        <h2 class="mb-3 text-sm font-semibold text-text">{t(panel.titleKey)}</h2>
        <PanelComponent data={panel.data} context={data.context} lookups={emptyLookups} />
      </section>
    {/if}
  {/each}
</div>

<ConfirmDialog
  bind:open={confirmIssue}
  title={t("invoicing.action.issue")}
  message={t("invoicing.issue_confirm")}
  action="?/issue"
/>
<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("invoicing.quote_delete_confirm")}
  action="?/delete"
/>
<ConfirmDialog
  bind:open={confirmConvert}
  title={t("invoicing.action.convert")}
  message={t("invoicing.issue_confirm")}
  action="?/convert"
/>

<Modal
  bind:open={decisionOpen}
  title={decisionAction === "accept" ? t("invoicing.action.accept") : t("invoicing.action.reject")}
>
  <form
    method="POST"
    action={decisionAction === "accept" ? "?/accept" : "?/reject"}
    use:enhance={busy.wrap("decision", () => ({ result, update }) => {
      if (result.type === "success") decisionOpen = false;
      void update({ reset: false });
    })}
    class="space-y-3"
  >
    <div>
      <label for="decision-note" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.send.message")}</label
      >
      <textarea id="decision-note" name="note" rows="3" class={inputClass}></textarea>
    </div>
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (decisionOpen = false)}>{t("common.cancel")}</button
      >
      <Button loading={busy.is("decision")} disabled={busy.active}>
        {decisionAction === "accept" ? t("invoicing.action.accept") : t("invoicing.action.reject")}
      </Button>
    </div>
  </form>
</Modal>

<Modal bind:open={sendOpen} title={t("invoicing.send.title")}>
  <form
    method="POST"
    action="?/send"
    use:enhance={busy.wrap("send", () => ({ result, update }) => {
      if (result.type === "success") sendOpen = false;
      void update({ reset: false });
    })}
    class="space-y-3"
  >
    <div>
      <label for="send-to" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.send.to")}</label
      >
      <input
        id="send-to"
        name="to"
        type="email"
        value={quote.customer?.email ?? ""}
        class={inputClass}
      />
    </div>
    <div>
      <label for="send-message" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.send.message")}</label
      >
      <textarea id="send-message" name="message" rows="3" class={inputClass}></textarea>
    </div>
    <label class="flex items-center gap-2 text-sm text-text">
      <input type="checkbox" name="email" value="1" checked class="rounded border-border" />
      {t("invoicing.send.email_toggle")}
    </label>
    <p class="text-xs text-text-muted">{t("invoicing.send.mark_only_hint")}</p>
    {#if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (sendOpen = false)}>{t("common.cancel")}</button
      >
      <Button loading={busy.is("send")} disabled={busy.active}>{t("invoicing.action.send")}</Button>
    </div>
  </form>
</Modal>

<ContactQuickCreate
  bind:open={qcContactOpen}
  name={qcContactName}
  pickerSlot="contact"
  definitions={data.contactDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>

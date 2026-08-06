<script lang="ts">
  import { Ban, Bell, Download, FileMinus, Pencil, Printer, Send, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { editIntent } from "$lib/core/edit-intent";
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { entityPanelsFor } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DocumentForm from "$lib/modules/invoicing/DocumentForm.svelte";
  import DocumentFrame from "$lib/core/ui/DocumentFrame.svelte";
  import PaymentIntentsCard from "$lib/modules/invoicing/PaymentIntentsCard.svelte";
  import { docMoney, docStatus } from "$lib/modules/invoicing/types";

  let { data, form } = $props();

  const invoice = $derived(data.invoice);
  const isDraft = $derived(invoice.status === "draft");
  const isCredit = $derived(invoice.kind === "credit_note");
  const docState = $derived(docStatus(invoice));
  // A credit note the invoice could not absorb is money going back to the client, so the
  // whole payment affordance changes voice: you register a refund, not a payment.
  const owesRefund = $derived(isCredit && Number(invoice.outstanding) < 0);
  // Withdrawing is about registered money, not about status: a fully applied credit note
  // rests at `paid` without a cent having moved, and is the one document still withdrawable.
  // An invoice a credit note wrote down is not, or that credit note would be left stranded.
  const canCancel = $derived(
    data.canWrite &&
      Number(invoice.paid_total) === 0 &&
      !invoice.credited &&
      (invoice.status === "open" || (isCredit && invoice.status === "paid")),
  );
  // The edit affordance opens on a row's ⋯ → Bewerken arrival (#78), then the user owns it.
  let editing = $state(editIntent());
  $effect(() => {
    if (form?.saved || form?.issued) editing = false;
  });

  const busy = new InFlight();
  let confirmIssue = $state(false);
  let confirmCancel = $state(false);
  let confirmDelete = $state(false);
  let confirmCredit = $state(false);
  let sendOpen = $state(false);
  let payOpen = $state(false);
  let deletePaymentId = $state("");
  let confirmDeletePayment = $state(false);
  let remindForm: HTMLFormElement | undefined = $state();

  // Inline-create from the edit form's contact picker (#115): "＋ … toevoegen" opens this dialog.
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
  // The invoice's client rides along (#247): the new contact links to it by default.
  let qcContactCompany = $state<{ id: string; name: string } | null>(null);

  const money = (value: string | number | null | undefined) =>
    docMoney(value, invoice.currency, data.locale);

  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const panelSpecs = $derived(entityPanelsFor(enabled, "invoice"));
  function panelComponent(key: string) {
    return panelSpecs.find((spec) => spec.key === key)?.component;
  }
  const emptyLookups = { members: [], companies: [], projects: [], tasks: [] };

  const title = $derived(invoice.number ?? t("invoicing.status.draft"));
  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(`${t("invoicing.kind.invoice")} ${title}`)}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <div class="flex min-w-0 items-center gap-3">
    <h1 class="truncate text-xl font-semibold text-text">
      {invoice.kind === "credit_note"
        ? t("invoicing.kind.credit_note")
        : t("invoicing.kind.invoice")}
      {title}
    </h1>
    {#if docState.tone === "danger"}
      <span
        class="rounded-md bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300"
        >{t(docState.key)}</span
      >
    {:else}
      <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
        >{t(docState.key)}</span
      >
    {/if}
    <!-- Both halves of a correction link to each other: a credit note that names no invoice
         is a document you have to go looking for. -->
    {#if isCredit && invoice.credit_for_id}
      <a
        href="/invoices/{invoice.credit_for_id}"
        class="rounded-md bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand hover:underline"
      >
        {t("invoicing.field.credit_for")}
        {invoice.credit_for_number || ""}
      </a>
    {/if}
    {#each invoice.credit_notes ?? [] as note (note.id)}
      <a
        href="/invoices/{note.id}"
        class="rounded-md bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand hover:underline"
      >
        {t("invoicing.field.credited_by")}
        {note.number || t("invoicing.status.draft")}
      </a>
    {/each}
    <a
      href="/companies/{invoice.company_id}"
      class="truncate text-sm text-text-muted hover:text-brand">{invoice.company_name}</a
    >
    {#if invoice.subscription_id && data.canReadRegister}
      <!-- Provenance (owner feedback): a subscription-cycle draft says so, with its period.
           A link into the agency's subscription register, so it follows the same gate as the
           rest of the section's own chrome (#266) — a client's invoice may well come from a
           recurring agreement, and the chip would have been a guaranteed 403 for them. -->
      <a
        href="/subscriptions"
        class="rounded-md bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand hover:underline"
        title={t("invoicing.from_subscription_hint")}
      >
        {t("invoicing.from_subscription", {
          period: [invoice.period_start, invoice.period_end]
            .filter(Boolean)
            .map((d) => fmtNumericDate(String(d)))
            .join("–"),
        })}
      </a>
    {/if}
  </div>
  <div class="flex items-center gap-2">
    {#if isDraft && data.canWrite}
      <button
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        onclick={() => (confirmIssue = true)}>{t("invoicing.action.issue")}</button
      >
    {/if}
    {#if invoice.status === "open"}
      {#if data.canSend}
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          onclick={() => (sendOpen = true)}>{t("invoicing.action.send")}</button
        >
      {/if}
      {#if data.canPay}
        <button
          class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand"
          onclick={() => (payOpen = true)}
          >{t(
            owesRefund ? "invoicing.action.register_refund" : "invoicing.action.register_payment",
          )}</button
        >
      {/if}
    {/if}
    <ActionsMenu
      items={[
        ...(data.canWrite && (isDraft || invoice.status === "open")
          ? [{ label: t("common.edit"), icon: Pencil, onclick: () => (editing = !editing) }]
          : []),
        ...(!isDraft
          ? [
              {
                label: t("invoicing.action.download_pdf"),
                icon: Download,
                href: `/invoices/${invoice.id}/pdf`,
              },
              {
                label: t("invoicing.action.print"),
                icon: Printer,
                href: `/invoices/${invoice.id}/print`,
              },
              {
                label: t("invoicing.action.download_ubl"),
                icon: Download,
                href: `/invoices/${invoice.id}/ubl`,
              },
            ]
          : []),
        ...(invoice.status === "open" && data.canSend
          ? [
              {
                label: t("invoicing.action.remind"),
                icon: Bell,
                onclick: () => remindForm?.requestSubmit(),
              },
            ]
          : []),
        // A credit note is not credited (you re-bill with an invoice), and neither is one
        // already written off in full — both 409 at the API, so neither is drawn.
        ...(data.canWrite &&
        !isCredit &&
        !invoice.fully_credited &&
        (invoice.status === "open" || invoice.status === "paid")
          ? [
              {
                label: t("invoicing.action.credit"),
                icon: FileMinus,
                onclick: () => (confirmCredit = true),
              },
            ]
          : []),
        ...(canCancel
          ? [
              {
                label: t("invoicing.action.cancel"),
                icon: Ban,
                danger: true,
                onclick: () => (confirmCancel = true),
              },
            ]
          : []),
        ...(data.canDelete && isDraft
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
{#if invoice.sent_at}
  <p class="mb-4 text-xs text-text-muted">
    {t("invoicing.sent_at", { date: fmtDateTime(invoice.sent_at) })}
    {#if invoice.reminder_count > 0 && invoice.last_reminder_at}
      · {t("invoicing.reminded_at", {
        count: invoice.reminder_count,
        date: fmtDateTime(invoice.last_reminder_at),
      })}
    {/if}
  </p>
{/if}

<div class="grid gap-6 xl:grid-cols-[1fr_20rem]">
  <div class="min-w-0 space-y-6">
    {#if editing}
      <div class="rounded-xl border border-border bg-surface-raised p-6">
        <DocumentForm
          kind="invoice"
          doc={invoice}
          action="?/save"
          contacts={data.contacts}
          taxRates={data.taxRates}
          products={data.products}
          templates={data.templates}
          settings={data.settings}
          locale={data.locale}
          {form}
          oncancel={() => (editing = false)}
          oncreatecontact={(name, company) => {
            qcContactName = name;
            qcContactCompany = company;
            qcContactOpen = true;
          }}
        />
        <!-- The API refuses money edits after issue; this marker tells the save action to
             send only the process fields. -->
        <input type="hidden" name="_status" value={invoice.status} form="doc-form-invoice" />
      </div>
    {:else}
      <DocumentFrame
        src="/invoices/{invoice.id}/preview"
        version={invoice.updated_at}
        title={`${t("invoicing.kind.invoice")} ${invoice.number ?? ""}`}
        class="mx-auto max-w-3xl"
      />
    {/if}

    <!-- History hangs last (UX §4): the trail panel is core-contributed. -->
    {#each data.panels as panel (panel.key)}
      {@const PanelComponent = panelComponent(panel.key)}
      {#if PanelComponent}
        <section class="rounded-xl border border-border bg-surface-raised p-4">
          <h2 class="mb-3 text-sm font-semibold text-text">{t(panel.titleKey)}</h2>
          <PanelComponent data={panel.data} context={data.context} lookups={emptyLookups} />
        </section>
      {/if}
    {/each}
  </div>

  <aside class="space-y-4">
    <div class="rounded-xl border border-border bg-surface-raised p-4">
      <h2 class="mb-2 text-sm font-semibold text-text">
        {t(owesRefund ? "invoicing.field.refund_due" : "invoicing.field.outstanding")}
      </h2>
      <p
        class="text-2xl font-semibold {invoice.overdue
          ? 'text-red-600 dark:text-red-400'
          : 'text-text'}"
      >
        {money(owesRefund ? -Number(invoice.outstanding) : invoice.outstanding)}
      </p>
      <dl class="mt-2 space-y-1 text-sm">
        <div class="flex justify-between">
          <dt class="text-text-muted">{t("invoicing.field.total")}</dt>
          <dd class="tabular-nums text-text">{money(invoice.total)}</dd>
        </div>
        <!-- The second way a balance comes down. Shown only when it did, so an ordinary
             invoice keeps the two-line card it always had. -->
        {#if Number(invoice.credited_total) !== 0}
          <div class="flex justify-between">
            <dt class="text-text-muted">{t("invoicing.field.credited")}</dt>
            <dd class="tabular-nums text-text">{money(-Number(invoice.credited_total))}</dd>
          </div>
        {/if}
        {#if isCredit && Number(invoice.applied_total) !== 0}
          <div class="flex justify-between">
            <dt class="text-text-muted">
              {t("invoicing.field.applied", { number: invoice.credit_for_number || "" })}
            </dt>
            <dd class="tabular-nums text-text">{money(invoice.applied_total)}</dd>
          </div>
        {/if}
        {#if invoice.due_date}
          <div class="flex justify-between">
            <dt class="text-text-muted">{t("invoicing.field.due_date")}</dt>
            <dd
              class="tabular-nums {invoice.overdue
                ? 'text-red-600 dark:text-red-400'
                : 'text-text'}"
            >
              {fmtNumericDate(invoice.due_date)}
            </dd>
          </div>
        {/if}
      </dl>
      {#if invoice.status === "open" && data.canWrite}
        <form method="POST" action="?/save" use:enhance class="mt-3 border-t border-border pt-3">
          <input type="hidden" name="_status" value={invoice.status} />
          <label class="flex items-center gap-2 text-sm text-text">
            <FormCheckbox
              name="reminders_paused_toggle"
              checked={invoice.reminders_paused}
              onchange={(e) => e.currentTarget.form?.requestSubmit()}
              class="rounded border-border"
            />
            {t("invoicing.field.reminders_paused")}
          </label>
          <input
            type="hidden"
            name="reminders_paused"
            value={invoice.reminders_paused ? "0" : "1"}
          />
        </form>
      {/if}
    </div>

    <!-- Online payment (#267) sits directly under the balance, because that is the number it
         collects and the client reads the two together: what is due, and the button that pays
         it. The ledger below stays what it always was — every payment, however it arrived. The
         card draws nothing at all when there is neither an attempt nor one to offer, so an
         instance with no payment provider connected is unchanged. -->
    <PaymentIntentsCard
      intents={invoice.intents ?? []}
      currency={invoice.currency}
      locale={data.locale}
      payable={invoice.status === "open" && invoice.online_payment}
      canStart={data.canStartPayment}
      canSync={data.canSyncPayment}
      agencyView={data.canReadRegister}
      {form}
    />

    <div class="rounded-xl border border-border bg-surface-raised p-4">
      <h2 class="mb-2 text-sm font-semibold text-text">{t("invoicing.payment.history")}</h2>
      {#if (invoice.payments ?? []).length === 0}
        <p class="text-sm text-text-muted">{t("invoicing.payment.empty")}</p>
      {:else}
        <ul class="divide-y divide-border">
          {#each invoice.payments as payment (payment.id)}
            <li class="flex items-center justify-between gap-2 py-2 text-sm">
              <div>
                <span class="tabular-nums text-text">{money(payment.amount)}</span>
                <span class="ml-2 text-xs text-text-muted">
                  {fmtNumericDate(payment.paid_on)} ·
                  {t(`invoicing.payment.method.${payment.method}`)}
                </span>
                {#if payment.note}
                  <p class="text-xs text-text-muted">{payment.note}</p>
                {/if}
              </div>
              {#if data.canPay}
                <ActionsMenu
                  compact
                  items={[
                    {
                      label: t("common.delete"),
                      icon: Trash2,
                      danger: true,
                      onclick: () => {
                        deletePaymentId = payment.id;
                        confirmDeletePayment = true;
                      },
                    },
                  ]}
                />
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </aside>
</div>

<!-- Remind rides a tiny named form so the ⋯ item can submit it. -->
<form bind:this={remindForm} method="POST" action="?/remind" use:enhance class="hidden"></form>

<ConfirmDialog
  bind:open={confirmIssue}
  title={t("invoicing.action.issue")}
  message={t("invoicing.issue_confirm")}
  action="?/issue"
  confirmLabel={t("invoicing.action.issue")}
  variant="primary"
/>
<ConfirmDialog
  bind:open={confirmCancel}
  title={t("invoicing.action.cancel")}
  message={t("invoicing.cancel_confirm")}
  action="?/cancel"
  confirmLabel={t("invoicing.action.cancel")}
/>
<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("invoicing.delete_confirm")}
  action="?/delete"
/>
<ConfirmDialog
  bind:open={confirmCredit}
  title={t("invoicing.action.credit")}
  message={t("invoicing.credit_confirm")}
  action="?/credit"
  confirmLabel={t("invoicing.action.credit")}
  variant="primary"
/>
<ConfirmDialog
  bind:open={confirmDeletePayment}
  title={t("common.delete")}
  message={t("invoicing.payment.delete_confirm")}
  action="?/deletePayment"
  fields={{ payment_id: deletePaymentId }}
/>

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
        value={invoice.customer?.email ?? ""}
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
      <Button loading={busy.is("send")} disabled={busy.active}>
        <Send size={14} class="mr-1 inline" />{t("invoicing.action.send")}
      </Button>
    </div>
  </form>
</Modal>

<Modal
  bind:open={payOpen}
  title={t(owesRefund ? "invoicing.action.register_refund" : "invoicing.payment.title")}
>
  <form
    method="POST"
    action="?/payment"
    use:enhance={busy.wrap("payment", () => ({ result, update }) => {
      if (result.type === "success") payOpen = false;
      void update({ reset: false });
    })}
    class="space-y-3"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="pay-date" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.payment.date")}</label
        >
        <DateInput name="paid_on" id="pay-date" required value="" />
      </div>
      <div>
        <label for="pay-amount" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.payment.amount")}</label
        >
        <input
          id="pay-amount"
          name="amount"
          type="number"
          step="any"
          required
          value={invoice.outstanding}
          class={inputClass}
        />
      </div>
    </div>
    <div>
      <label for="pay-method" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.payment.method")}</label
      >
      <!-- `online` is deliberately not offered here, and `PaymentWrite.method` is a closed
           Literal that refuses it (#267): that value means "a provider confirmed this", and
           nobody should be able to assert it by hand. An online payment writes its own ledger
           row from the callback; this modal is for money that arrived some other way. -->
      <select id="pay-method" name="method" class={inputClass}>
        {#each ["bank", "cash", "card", "other"] as method (method)}
          <option value={method}>{t(`invoicing.payment.method.${method}`)}</option>
        {/each}
      </select>
    </div>
    <div>
      <label for="pay-note" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.payment.note")}</label
      >
      <input id="pay-note" name="note" class={inputClass} />
    </div>
    {#if form?.error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (payOpen = false)}>{t("common.cancel")}</button
      >
      <Button loading={busy.is("payment")} disabled={busy.active}>{t("common.save")}</Button>
    </div>
  </form>
</Modal>

<ContactQuickCreate
  bind:open={qcContactOpen}
  name={qcContactName}
  linkCompany={qcContactCompany}
  pickerSlot="contact"
  definitions={data.contactDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>

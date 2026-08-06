<script lang="ts">
  /**
   * Online payment of an invoice, on the invoice screen (epic #269, issue #267): what has been
   * attempted, what state each attempt is in, and — where it is allowed — starting another one.
   *
   * The card belongs to the invoicing module rather than being typed into the detail route, for
   * the reason `PortalCard` exists: one surface renders for two audiences, and the rules that
   * keep it honest for both are worth having in one place instead of scattered through a page
   * that is already mostly document chrome.
   *
   * Four decisions, each with a plausible wrong version:
   *
   * * **Every control gates on the API's own permission key, never on `!isPortal`.** The client
   *   portal renders this exact component (docs/UX.md, the client-portal entry), and for a
   *   client the pay button is the *point* — `!isPortal` would hide the one control the screen
   *   exists for. Starting a payment is `invoicing.payment.link` at the route's floor, because
   *   the `client` role holds it at `:own`; re-asking the provider is the same key at `:any`,
   *   because that is a repair action which spends an outbound call on every press and a
   *   client's own status arrives by callback and, failing that, by the hourly reconcile.
   * * **A paid attempt that booked nothing says so, loudly.** `paid` with no `settled_at` is
   *   money that arrived while the ledger row did not get written — a webhook that never
   *   reached us, an access proxy in front of the callback path (docs/DEPLOY.md). Silence is
   *   the worst answer available there: the invoice reads open, the client has paid, and nobody
   *   is looking. A **test** attempt rests in exactly that state by design, so it gets its own
   *   line instead; telling an agency to "record it by hand" for money that does not exist
   *   would be worse than saying nothing at all.
   * * **The voice follows `agencyView` — the same scoped key the list screen names itself
   *   with** (#266, `invoicing.invoice.read:any`). The agency creates a *betaallink* to send on;
   *   the person who owes the money presses *Nu betalen*. That is an audience difference, not a
   *   permission difference, so it hangs off "may you read the whole register" rather than
   *   growing a second gate that means the same thing less precisely.
   * * **The card disappears when it has nothing to say.** An agency with no payment provider
   *   connected, or a draft, or a long-settled invoice that was never paid online, would
   *   otherwise carry a permanently empty box on every document forever. The empty state is for
   *   "you can start one and have not yet", which is a real thing to tell someone.
   *
   * Nothing here fetches: `intents` rides the invoice's **detail** read, which the page already
   * loaded (docs/PERFORMANCE.md — a list draws none of this and pays for none of it).
   */
  import { Copy, ExternalLink, RefreshCw } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  import { docMoney } from "./types";

  import type { PaymentIntent } from "./types";

  let {
    intents,
    currency,
    locale,
    payable,
    canStart,
    canSync,
    agencyView,
    form,
  }: {
    /** This invoice's attempts, newest first (the API orders them). */
    intents: PaymentIntent[];
    /** The **document's** currency, which may deviate from the org's. */
    currency: string;
    locale: string;
    /** `InvoiceRead.online_payment`: a credential is connected *and* something is collectable.
     *  Derived server-side precisely so the portal can draw this button without being allowed
     *  to read which accounts the agency has connected. */
    payable: boolean;
    /** `invoicing.payment.link` — the route's floor, so a client's `:own` satisfies it. */
    canStart: boolean;
    /** `invoicing.payment.link:any` — the repair action, staff only. */
    canSync: boolean;
    /** `invoicing.invoice.read:any`: this viewer reads the agency's register, so speak to them
     *  as the agency. Naming, never gating. */
    agencyView: boolean;
    /**
     * The host route's whole `form` result. Untyped beyond "an object" for the same reason
     * `PortalCard` does it: a page's `ActionData` is the union of *every* action on it, so a
     * narrow type here would only ever match one host.
     */
    form?: Record<string, unknown> | null;
  } = $props();

  // The two payment actions report here rather than into the page's own error line: a refusal
  // ("no provider connected", "more than one account — say which") belongs beside the button
  // that earned it, not four cards further up the page.
  const paymentError = $derived(typeof form?.paymentError === "string" ? form.paymentError : null);

  const money = (value: string | number | null | undefined) => docMoney(value, currency, locale);

  // Sibling forms that all touch this invoice's payments, so only one runs at a time (#242):
  // "start" plus one key per attempt for its own status check.
  const busy = new InFlight();

  // Copy feedback, the house clipboard pattern (Instellingen → Domein, → SSO): the label swaps
  // for a moment and reverts, keyed by attempt so two rows never both read "gekopieerd".
  let copiedId = $state<string | null>(null);
  async function copyLink(intent: PaymentIntent) {
    if (!intent.checkout_url) return;
    try {
      await navigator.clipboard.writeText(intent.checkout_url);
      copiedId = intent.id;
      setTimeout(() => (copiedId = null), 2000);
    } catch {
      copiedId = null;
    }
  }

  /** How a state reads. Waiting is amber because it is not finished; `authorized` is money
   *  reserved and not yet captured, which is the same "not finished" to a reader. `expired`
   *  and `canceled` are ordinary — clients abandon checkouts and it means nothing — so they
   *  stay quiet, while a genuine failure does not. */
  function tone(status: PaymentIntent["status"]): string {
    if (status === "paid")
      return "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300";
    if (status === "failed") return "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300";
    if (status === "open" || status === "pending" || status === "authorized")
      return "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300";
    return "text-text-muted ring-1 ring-inset ring-border";
  }

  /** Money in, ledger row missing. See the header: a test attempt lives here by design and is
   *  told apart rather than nagged about. */
  const unsettled = (intent: PaymentIntent) =>
    intent.status === "paid" && intent.settled_at === null && intent.mode !== "test";

  const canOffer = $derived(canStart && payable);
  const visible = $derived(intents.length > 0 || canOffer);
</script>

{#if visible}
  <section class="rounded-xl border border-border bg-surface-raised p-4">
    <h2 class="mb-2 text-sm font-semibold text-text">{t("invoicing.intents.title")}</h2>

    {#if paymentError}
      <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(paymentError)}</p>
    {/if}

    {#if canOffer}
      <form method="POST" action="?/startPayment" use:enhance={busy.wrap("start")}>
        <Button size="sm" loading={busy.is("start")} disabled={busy.active}>
          {agencyView ? t("invoicing.intents.new") : t("invoicing.intents.pay_now")}
        </Button>
      </form>
      {#if agencyView}
        <p class="mt-2 text-xs text-text-muted">{t("invoicing.intents.new_hint")}</p>
      {/if}
    {/if}

    {#if intents.length === 0}
      <p class="mt-3 text-sm text-text-muted">{t("invoicing.intents.empty")}</p>
    {:else}
      <ul class="mt-3 divide-y divide-border border-t border-border">
        {#each intents as intent (intent.id)}
          <li class="space-y-2 py-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {tone(intent.status)}">
                {t(`invoicing.intents.status.${intent.status}`)}
              </span>
              <span class="tabular-nums text-sm text-text">{money(intent.amount)}</span>
            </div>
            <p class="text-xs text-text-muted">
              {t("invoicing.intents.created", { when: fmtDateTime(intent.created_at) })}
              <!-- The method is the provider's own word for it (`ideal`, `creditcard`), so it
                   is shown as given: these are brand names, not UI text to translate, and a
                   lookup table here would go stale the day the provider adds one. -->
              {#if intent.method}
                · {intent.method}
              {/if}
            </p>

            {#if intent.mode === "test"}
              <p class="text-xs text-amber-700 dark:text-amber-400">
                {t("invoicing.intents.test_mode")}
              </p>
            {/if}
            {#if unsettled(intent)}
              <p class="text-xs font-medium text-amber-700 dark:text-amber-400">
                {t("invoicing.intents.unsettled")}
              </p>
            {/if}
            <!-- The provider's own refusal, verbatim and untranslated: it is the only thing
                 that says *why*, and paraphrasing it into one of our keys would lose exactly
                 the detail an operator needs to fix it. -->
            {#if intent.last_error}
              <p class="break-words text-xs text-red-600 dark:text-red-400">{intent.last_error}</p>
            {/if}

            <div class="flex flex-wrap items-center gap-2">
              <!-- Present on a fresh attempt and gone once it can no longer be paid, so this
                   pair appears exactly while it is worth pressing. -->
              {#if intent.checkout_url}
                <a
                  href={intent.checkout_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  class="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                >
                  <ExternalLink size={13} aria-hidden="true" />
                  {t("invoicing.intents.open")}
                </a>
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  onclick={() => copyLink(intent)}
                >
                  <Copy size={13} aria-hidden="true" />
                  {copiedId === intent.id
                    ? t("invoicing.intents.copied")
                    : t("invoicing.intents.copy")}
                </Button>
              {/if}
              {#if canSync}
                <form method="POST" action="?/syncPayment" use:enhance={busy.wrap(intent.id)}>
                  <input type="hidden" name="intent_id" value={intent.id} />
                  <Button
                    variant="secondary"
                    size="xs"
                    loading={busy.is(intent.id)}
                    disabled={busy.active}
                  >
                    <RefreshCw size={13} aria-hidden="true" />
                    {t("invoicing.intents.sync")}
                  </Button>
                </form>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

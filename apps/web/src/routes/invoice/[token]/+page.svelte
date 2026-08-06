<script lang="ts">
  /**
   * The invoice a client opens from a link (#304) — the screen behind the QR on the paper.
   *
   * Four things shape it, and each is a decision rather than a style.
   *
   * **It is not the app.** No sidebar, no nav, nothing to click into. A page with a menu invites
   * a visitor to try the menu, and every one of those doors is a 401 — a screen full of controls
   * that refuse (#253) is worse than a screen with one control that works. What it does carry is
   * the tenant's branding, because a page asking for money has to look like the people who sent
   * the invoice (Golden Rule 4, and the theme resolves for anonymous requests already).
   *
   * **The document is the document.** Framed from the API's own renderer, so the page a client
   * scans into is byte-identical to the PDF and to what staff see. Redrawing an invoice in
   * Svelte for this audience would have been a second implementation of a legal document.
   *
   * **The pay button is a form.** It survives having no JavaScript, because this page is opened
   * by whatever browser was behind a phone camera. Hydration turns it into a redirect the
   * moment the action answers.
   *
   * **A returning payer is told what is happening.** The server already asked the provider once
   * before rendering (`+page.server.ts`); if the attempt is still in flight the page keeps
   * asking, on a bounded schedule, and stops the moment the answer is final. That polling is
   * the *only* reason this component has an effect in it.
   */
  import { invalidateAll } from "$app/navigation";
  import { page } from "$app/state";
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { fmtNumericDate } from "$lib/core/format";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import DocumentFrame from "$lib/core/ui/DocumentFrame.svelte";
  import { docMoney } from "$lib/modules/invoicing/types";

  let { data, form } = $props();

  const busy = new InFlight();
  const invoice = $derived(data.invoice);
  const brand = $derived(page.data.theme?.brandName || "");
  const money = (value: string | number | null | undefined) =>
    docMoney(value, invoice.currency, invoice.locale || "nl");

  /**
   * The pay action answers with a 303 to the provider's checkout — a location that is off-site,
   * which `enhance`'s default handler cannot take (`goto` refuses an external URL). So this
   * takes the redirect itself and leaves everything else to the default.
   *
   * With JavaScript off the same action is a plain form post and the browser follows the 303 on
   * its own, which is why it is a redirect rather than a URL in the response body: the page is
   * opened by whatever browser sat behind a phone camera, and the pay button has to work there.
   */
  const payEnhance = busy.wrap("pay", () => async ({ result, update }) => {
    if (result.type === "redirect") {
      window.location.href = result.location;
      return;
    }
    await update();
  });

  /**
   * Waiting for the money to be confirmed. `payment_pending` is the API's own read of the
   * latest attempt, so the page never has to decide what "in flight" means.
   */
  const pending = $derived(data.returning && invoice.payment_pending && invoice.status !== "paid");

  /**
   * Poll while an attempt is in flight, and stop.
   *
   * Bounded three ways, because an unattended tab must not poll a payment provider forever:
   * a fixed number of attempts, a two-second gap, and the condition above going false. The API
   * throttles its own outbound calls independently (one per attempt per five seconds), so the
   * gap here is about how fast the *screen* updates, not about how hard the provider is hit.
   */
  let polls = $state(0);
  $effect(() => {
    if (!pending || polls >= 10) return;
    const timer = setTimeout(async () => {
      polls += 1;
      await fetch(`/invoice/${data.token}/refresh`, { method: "POST" });
      await invalidateAll();
    }, 2000);
    return () => clearTimeout(timer);
  });

  const statusTone = $derived(
    invoice.status === "paid"
      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
      : invoice.status === "cancelled"
        ? "text-text-muted ring-1 ring-inset ring-border"
        : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  );
</script>

<svelte:head>
  <title>{pageTitle(invoice.number || t("invoicing.public.document"))}</title>
  <!-- Belt and braces with the API's own `X-Robots-Tag`: a link mailed to a client ends up in
       signatures, tickets and helpdesk threads, and an invoice must never reach an index. -->
  <meta name="robots" content="noindex, nofollow, noarchive" />
  <meta name="referrer" content="no-referrer" />
</svelte:head>

<div class="mx-auto min-h-screen w-full max-w-3xl px-4 py-8">
  <header class="mb-6 flex flex-wrap items-center justify-between gap-4">
    <div class="flex items-center gap-3">
      {#if page.data.theme?.logoUrl}
        <img src={page.data.theme.logoUrl} alt={brand} class="h-9" />
      {:else if brand}
        <span class="text-lg font-semibold text-text">{brand}</span>
      {/if}
    </div>
    <span class="rounded-full px-2.5 py-1 text-xs font-medium {statusTone}">
      {t(`invoicing.status.${invoice.status}`)}
    </span>
  </header>

  <section class="mb-6 rounded-2xl border border-border bg-surface-raised p-5">
    <h1 class="text-base font-semibold text-text">
      {t("invoicing.public.heading", { number: invoice.number })}
    </h1>
    {#if invoice.customer_name}
      <p class="mt-0.5 text-sm text-text-muted">{invoice.customer_name}</p>
    {/if}

    <dl class="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
      <div>
        <dt class="text-xs text-text-muted">{t("invoicing.field.total")}</dt>
        <dd class="tabular-nums text-sm text-text">{money(invoice.total)}</dd>
      </div>
      <div>
        <dt class="text-xs text-text-muted">{t("invoicing.field.outstanding")}</dt>
        <dd class="tabular-nums text-sm font-medium text-text">{money(invoice.outstanding)}</dd>
      </div>
      {#if invoice.due_date}
        <div>
          <dt class="text-xs text-text-muted">{t("invoicing.field.due_date")}</dt>
          <dd class="text-sm text-text">{fmtNumericDate(invoice.due_date)}</dd>
        </div>
      {/if}
    </dl>

    {#if invoice.status === "paid"}
      <p class="mt-4 text-sm text-emerald-700 dark:text-emerald-400">
        {t("invoicing.public.paid")}
      </p>
    {:else if pending}
      <!-- The payer is back from the checkout and the provider has not confirmed yet. Saying so
           is the entire point: silence here reads as "my payment did not go through". -->
      <p class="mt-4 text-sm text-amber-700 dark:text-amber-400" role="status">
        {t("invoicing.public.confirming")}
      </p>
    {/if}

    {#if form?.error}
      <p class="mt-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}

    <div class="mt-5 flex flex-wrap items-center gap-2">
      {#if invoice.payable && !pending}
        <form method="POST" action="?/pay" use:enhance={payEnhance}>
          <Button loading={busy.is("pay")} disabled={busy.active}>
            {t("invoicing.public.pay")}
          </Button>
        </form>
      {/if}
      <!-- A plain link, not a fetch: the browser's own download handling is what a client
           wants here, and it works with nothing hydrated. -->
      <a
        href="/invoice/{data.token}/pdf"
        class="inline-flex items-center rounded-lg border border-border px-3 py-2 text-sm font-medium text-text hover:bg-surface"
      >
        {t("invoicing.public.download")}
      </a>
    </div>
  </section>

  <DocumentFrame
    src="/invoice/{data.token}/preview"
    version={invoice.status + invoice.paid_total}
    title={t("invoicing.public.document")}
    class="rounded-2xl border border-border bg-white"
  />

  <p class="mt-6 text-center text-xs text-text-muted">
    {t("invoicing.public.footer", { brand })}
  </p>
</div>

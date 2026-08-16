<script lang="ts">
  /**
   * "Are this client's books in step?" — the snelstart panel on a company detail page.
   *
   * The API contributes this panel as a server `PanelSpec` (`app/integrations/snelstart/panels.py`),
   * so what arrives here is the dict that provider returned; this file is only the drawing. Without
   * it the company hub falls back to a raw `<pre>{JSON}</pre>`, which is why an API panel still
   * needs a web counterpart even when it contributes no screen of its own.
   *
   * It reads stored links and never calls SnelStart — the provider says so and this side must not
   * quietly undo it by fetching something. A company page is opened all day; a bookkeeping API is
   * not on that path.
   *
   * Read-only on purpose. Pairing a relation, pushing an invoice and running a sync are acts on
   * *the administration*, they are gated on two other permissions, and they live on Instellingen →
   * SnelStart where the credential is. What belongs here is the answer, plus the one link that
   * takes somebody to where they can do something about it.
   */
  import { AlertTriangle, BookOpenCheck } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  let { data }: { data: Record<string, unknown> } = $props();

  const linked = $derived(Boolean(data.linked));
  const relationCode = $derived((data.relation_code as string | null) ?? null);
  const relationName = $derived((data.relation_name as string | null) ?? null);
  const relationStatus = $derived((data.relation_status as string | null) ?? null);
  const administration = $derived((data.administration as string | null) ?? null);
  const lastSyncedAt = $derived((data.last_synced_at as string | null) ?? null);
  const lastError = $derived((data.last_error as string | null) ?? null);
  const pending = $derived(Number(data.invoices_pending ?? 0));
  const failed = $derived(Number(data.invoices_failed ?? 0));
  const invoices = $derived((data.invoices ?? {}) as Record<string, number>);
  const booked = $derived(Number(invoices.active ?? 0));
  /**
   * Booked, and SnelStart no longer agrees with us about it — a boeking under this invoice's
   * number that we adopted rather than overwrote, for a different amount.
   *
   * It has its own number rather than being folded into "geweigerd", because nothing failed and
   * there is nothing to retry: somebody has to decide which of the two figures is right. Left
   * out of the panel entirely (which is what it was), the one screen an account manager opens
   * about this client showed three zeroes over a ledger that disagreed with the invoice.
   */
  const drift = $derived(Number(invoices.drift ?? 0));

  /**
   * `drift` is a state the tenant's own bookkeeper creates by editing the record in SnelStart, so
   * it is reported and never treated as a fault: the panel names it, and nothing here overwrites
   * it (the rule `cloudflare` states about observed redirects).
   */
  const statusTone: Record<string, string> = {
    active: "text-text",
    pending: "text-text-muted",
    drift: "text-amber-600",
    missing: "text-amber-600",
    error: "text-red-600",
    unlinked: "text-text-muted",
  };
</script>

{#if !linked && !relationCode}
  <p class="text-sm text-text-muted">{t("snelstart.panel.unlinked")}</p>
{:else}
  <div class="flex items-start gap-2">
    <BookOpenCheck size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium text-text">
        {relationName ?? t("snelstart.panel.relation_unnamed")}
        {#if relationCode}
          <span class="font-normal text-text-muted">({relationCode})</span>
        {/if}
      </p>
      <p class="mt-0.5 text-xs text-text-muted">
        {#if administration}{administration}{:else}{t("snelstart.panel.no_administration")}{/if}
        {#if relationStatus}
          · <span class={statusTone[relationStatus] ?? "text-text-muted"}
            >{t(`snelstart.link_status.${relationStatus}`)}</span
          >
        {/if}
        {#if lastSyncedAt}
          · {t("snelstart.panel.synced_at", { when: fmtDateTime(lastSyncedAt) })}
        {/if}
      </p>
    </div>
  </div>

  <!-- Four numbers, and the three that need a person come with a colour. "In de boekhouding" is
       the reassuring one; pending means a push has not happened yet, "wijkt af" means the ledger
       and the invoice disagree, and failed means one was refused and somebody has to read why. -->
  <dl class="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
    <div>
      <dt class="text-xs text-text-muted">{t("snelstart.panel.invoices_booked")}</dt>
      <dd class="text-text">{booked}</dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("snelstart.panel.invoices_pending")}</dt>
      <dd class={pending > 0 ? "text-amber-600" : "text-text"}>{pending}</dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("snelstart.panel.invoices_drift")}</dt>
      <dd class={drift > 0 ? "text-amber-600" : "text-text"}>{drift}</dd>
    </div>
    <div>
      <dt class="text-xs text-text-muted">{t("snelstart.panel.invoices_failed")}</dt>
      <dd class={failed > 0 ? "text-red-600" : "text-text"}>{failed}</dd>
    </div>
  </dl>

  {#if lastError}
    <!-- SnelStart's own untranslatable words, verbatim: they name the actual problem, and a house
         sentence in their place would say less. Shown on the client's page rather than only in the
         sync log, because "why is this one client not syncing?" is asked from here. -->
    <p class="mt-3 flex items-start gap-1.5 break-words text-xs text-red-600">
      <AlertTriangle size={14} class="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{lastError}</span>
    </p>
  {/if}

  <a class="mt-3 inline-block text-sm text-brand hover:underline" href="/settings/snelstart">
    {t("snelstart.panel.open_settings")}
  </a>
{/if}

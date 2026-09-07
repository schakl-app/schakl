<script lang="ts">
  /**
   * The agreements that keep this website online — hosting, maintenance, whatever kinds the
   * tenant has marked as covering a website (`subscription_types.covers_websites`).
   *
   * A website already said where it *runs* (its hosting account) and never who *pays* for that,
   * so "which agreement bills this site" was answered by opening the client's list and guessing
   * by name. This is the answer in place, and it is also where the link is made: the picker
   * offers the client's covering agreements not yet on this site (`linkable=true`, resolved by
   * the API so an agent's shortlist and a person's are the same), and "＋ nieuw abonnement"
   * opens the module's own dialog with the client and this website already filled in.
   *
   * Writes post to the host page's actions (`subscriptionLinkActions` + `subscriptionActions`,
   * `actions.server.ts`), the uptime panel's contract: SvelteKit actions live on the page.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { dateLocale, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { getLocale } from "$lib/paraglide/runtime";
  import { can } from "$lib/core/permissions";
  import type { EntityPanelContext } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import SubscriptionDialog from "./SubscriptionDialog.svelte";
  import type { Subscription } from "./types";

  let { data, context }: { data: unknown; context: EntityPanelContext; lookups?: unknown } =
    $props();

  const panel = $derived(
    (data ?? {}) as {
      subscriptions?: Subscription[];
      total?: number;
      /** Streamed: most visits never open the picker (docs/PERFORMANCE.md). */
      attachable?: Promise<Subscription[]>;
    },
  );
  const subscriptions = $derived(panel.subscriptions ?? []);
  const total = $derived(panel.total ?? subscriptions.length);
  const attachable = $derived(panel.attachable ?? Promise.resolve([] as Subscription[]));
  const companyId = $derived(context.companyId ?? "");

  // The key the call makes, not the one the panel is about (#310): both link routes declare
  // `subscriptions.subscription.write`.
  const canWrite = $derived(can(page.data.user, "subscriptions.subscription.write"));

  const busy = new InFlight();
  let picked = $state("");
  let attaching = $state(false);
  let creating = $state(false);

  function money(sub: Subscription): string {
    if (sub.amount == null) return "—";
    return new Intl.NumberFormat(dateLocale(), {
      style: "currency",
      currency: sub.currency || "EUR",
      trailingZeroDisplay: "stripIfInteger",
    }).format(Number(sub.amount));
  }

  function options(rows: Subscription[]) {
    return rows.map((sub) => ({
      value: sub.id,
      label: sub.name,
      hint: `${money(sub)} · ${t(`subscriptions.interval.${sub.interval}`)}`,
    }));
  }
</script>

{#if page.form?.subscriptionLinkError}
  <p class="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
    {t(page.form.subscriptionLinkError)}
  </p>
{/if}

{#if subscriptions.length === 0}
  <p class="text-sm text-text-muted">{t("subscriptions.panel.website_empty")}</p>
{:else}
  <PanelRows
    rows={subscriptions}
    {total}
    href={companyId ? `/subscriptions?company=${companyId}` : undefined}
    linkLabel={t("subscriptions.panel.view_all", { count: total })}
  >
    {#snippet children(shown)}
      <ul class="divide-y divide-border">
        {#each shown as sub (sub.id)}
          <li class="flex items-start justify-between gap-3 py-2">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <a
                  href={`/subscriptions/${sub.id}`}
                  class="truncate text-sm font-medium text-brand hover:underline">{sub.name}</a
                >
                <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
                  >{t(`subscriptions.status.${sub.status}`)}</span
                >
              </div>
              <p class="mt-0.5 text-xs text-text-muted tabular-nums">
                {money(sub)} · {t(`subscriptions.interval.${sub.interval}`)}
                {#if sub.next_invoice_date}
                  · {t("subscriptions.panel.next_invoice", {
                    date: fmtNumericDate(sub.next_invoice_date),
                  })}
                {/if}
              </p>
            </div>
            {#if canWrite}
              <!-- Detaching deletes nothing: the agreement keeps running and keeps billing, it
                   only stops claiming this site — so a button, not a confirm, and the picker
                   below puts it back. -->
              <form
                method="POST"
                action="?/subscriptionUnlink"
                use:enhance={busy.clear(`subscription-unlink-${sub.id}`)}
              >
                <input type="hidden" name="subscription_id" value={sub.id} />
                <input type="hidden" name="entity_type" value="website" />
                <Button type="submit" variant="secondary" disabled={busy.active}>
                  {t("subscriptions.link.detach")}
                </Button>
              </form>
            {/if}
          </li>
        {/each}
      </ul>
    {/snippet}
  </PanelRows>
{/if}

{#if canWrite}
  {#if attaching}
    <form
      method="POST"
      action="?/subscriptionLink"
      class="mt-3 flex flex-wrap items-end gap-2 border-t border-border pt-3"
      use:enhance={busy.wrap("subscription-link", () => async ({ result, update }) => {
        // A link made closes the picker; a refusal keeps it open with the reason above. The
        // picked id is spent either way, so the control starts a new choice (`reset: true`).
        if (result.type === "success") attaching = false;
        await update({ reset: true });
      })}
    >
      <input type="hidden" name="entity_type" value="website" />
      <div class="min-w-0 flex-1">
        <label class="mb-1 block text-sm font-medium text-text" for="website-subscription-pick">
          {t("subscriptions.link.pick")}
        </label>
        {#await attachable}
          <p class="text-xs text-text-muted">{t("common.loading")}</p>
        {:then rows}
          <!-- The ＋ is the picker's, wherever it is drawn (docs/UX.md): an unknown name opens
               the module's own dialog with this client and this site already on the form. -->
          <Combobox
            id="website-subscription-pick"
            name="subscription_id"
            bind:value={picked}
            items={options(rows)}
            placeholder={rows.length
              ? t("subscriptions.link.pick_placeholder")
              : t("subscriptions.link.none_attachable")}
            oncreate={() => (creating = true)}
          />
        {/await}
      </div>
      <Button type="submit" disabled={busy.active || !picked}>
        {t("subscriptions.link.attach")}
      </Button>
      <Button type="button" variant="secondary" onclick={() => (attaching = false)}>
        {t("common.cancel")}
      </Button>
    </form>
  {:else}
    <div class="mt-3 flex flex-wrap gap-2">
      <Button variant="secondary" onclick={() => (attaching = true)}>
        {t("subscriptions.link.attach_existing")}
      </Button>
      <Button variant="secondary" onclick={() => (creating = true)}>
        {t("subscriptions.add")}
      </Button>
    </div>
  {/if}

  <SubscriptionDialog
    bind:open={creating}
    {companyId}
    locale={getLocale()}
    defaultLinks={[
      { entity_type: "website", entity_id: context.entityId, label: context.label ?? "" },
    ]}
  />
{/if}

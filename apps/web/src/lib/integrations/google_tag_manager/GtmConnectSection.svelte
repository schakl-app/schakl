<script lang="ts">
  /**
   * "Attach a Tag Manager container to this client", mounted **inside** the marketing picker.
   *
   * The team asked for one control on a client's page that attaches every marketing source, Tag
   * Manager included (#411). Tag Manager is not a metrics source — see
   * `modules/marketing/types.ts` for why it is a *connection* instead — so it cannot ride
   * `MarketingAccountPicker`, and the marketing module may not import this file to mount it
   * (§6: a module importing an integration's internals is the one import direction this tree
   * does not have). So this registers itself as a `MarketingConnectorSpec` and the picker
   * composes it, exactly as the company hub composes panels it knows nothing about.
   *
   * It replaces `GtmLinkDialog`, which was a modal reached from the card the hub no longer
   * draws: a search box, because Tag Manager's quota is per user per minute and listing every
   * account's containers is one request per account (`GtmContainerSearch`).
   */
  import { enhance } from "$app/forms";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  import GtmContainerSearch from "./GtmContainerSearch.svelte";
  import type { GtmSearchHit } from "./types";

  let {
    action = "?/gtmLink",
    companyId = "",
    connectNext = "/",
    error = null,
  }: {
    action?: string;
    /**
     * The client, when the host action cannot read it off the route (`gtmConnectActions`).
     * Empty on a client's page — there `event.params.id` is the answer and a posted value would
     * be a second one free to disagree with it.
     */
    companyId?: string;
    connectNext?: string;
    error?: string | null;
  } = $props();

  const busy = new InFlight();
  let selected = $state<GtmSearchHit | null>(null);
</script>

<!-- The heading is the composer's, off `MarketingConnectorSpec.labelKey`, so a contributed
     surface cannot rename itself out of step with the group it sits in — the rule `ownsHeader`
     already states for panels (docs/UX.md). -->
<div class="space-y-2">
  {#if error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
  {/if}

  <GtmContainerSearch bind:selected {connectNext} hint={t("gtm.search.hint")} />

  <!-- clear(): linking starts something rather than editing a field the user typed into, so the
       next open should find an empty box rather than the last container's name. -->
  <form
    method="POST"
    {action}
    use:enhance={busy.wrap("gtmLink", () => async ({ update, result }) => {
      await update({ reset: true });
      if (result.type === "success") selected = null;
    })}
  >
    <input type="hidden" name="public_id" value={selected?.public_id ?? ""} />
    <input type="hidden" name="company_id" value={companyId} />
    <Button type="submit" disabled={busy.active || !selected}>
      {t("gtm.panel.link_container")}
    </Button>
  </form>
</div>

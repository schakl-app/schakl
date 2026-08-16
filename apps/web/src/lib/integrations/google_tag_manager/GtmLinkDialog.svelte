<script lang="ts">
  /**
   * "Connect a Tag Manager container to this client", wherever the question is asked.
   *
   * The client's page knows *which* client from the route, so this dialog only ever asks the half
   * the route cannot answer — which container — through the search box that is the whole point
   * (see `GtmContainerSearch`). It posts to the host page's own action, which is how every other
   * panel's edit mode writes (docs/UX.md).
   */
  import { enhance } from "$app/forms";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import GtmContainerSearch from "./GtmContainerSearch.svelte";
  import type { GtmSearchHit } from "./types";

  let {
    open = $bindable(false),
    action = "?/gtmLink",
    connectNext = "/",
    /** The host page's `form?.error`, so a refused link is read where it was asked for. */
    error = null,
  }: {
    open?: boolean;
    action?: string;
    connectNext?: string;
    error?: string | null;
  } = $props();

  const busy = new InFlight();
  let selected = $state<GtmSearchHit | null>(null);
</script>

<Modal bind:open title={t("gtm.panel.connect_title")}>
  <div class="space-y-4">
    {#if error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
    {/if}

    <GtmContainerSearch bind:selected {connectNext} hint={t("gtm.search.hint")} />

    <!-- clear(): linking starts something rather than editing a field the user typed into, and
         the dialog closes on success so a reset is what the next open should find. -->
    <form
      method="POST"
      {action}
      use:enhance={busy.wrap("gtmLink", () => async ({ update, result }) => {
        await update({ reset: true });
        if (result.type === "success") {
          open = false;
          selected = null;
        }
      })}
    >
      <input type="hidden" name="public_id" value={selected?.public_id ?? ""} />
      <Button type="submit" disabled={busy.active || !selected}>
        {t("gtm.panel.link_container")}
      </Button>
    </form>
  </div>
</Modal>

<script lang="ts">
  /** The pending-email review queue (#156) — the widget and the email_pending notification
   *  both land here. One aggregated list with the exact approve/reject/move actions the
   *  per-client panels already offer. */
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import InteractionsPanelBody from "$lib/modules/interactions/InteractionsPanelBody.svelte";

  let { data } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("interactions.review.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="text-xl font-semibold text-text">{t("interactions.review.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("interactions.review.subtitle")}</p>
</div>

<section class="max-w-3xl rounded-xl border border-border bg-surface-raised p-5">
  <InteractionsPanelBody items={data.items} total={data.total} members={data.members} />
</section>

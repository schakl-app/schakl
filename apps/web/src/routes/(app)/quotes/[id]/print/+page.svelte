<script lang="ts">
  /** The quote print surface — the invoice one's twin; see it for why the frame prints. */
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import DocumentFrame from "$lib/core/ui/DocumentFrame.svelte";

  let { data } = $props();

  const quote = $derived(data.quote);
  let frame = $state<ReturnType<typeof DocumentFrame> | null>(null);
</script>

<svelte:head>
  <title>{pageTitle(`${t("invoicing.quotes")} ${quote.number ?? ""}`)}</title>
</svelte:head>

<div class="mb-4 flex items-center gap-3">
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => frame?.print()}>{t("invoicing.action.print")}</button
  >
</div>

<DocumentFrame
  bind:this={frame}
  src="/quotes/{quote.id}/preview"
  version={quote.updated_at}
  title={`${t("invoicing.quotes")} ${quote.number ?? ""}`}
  class="mx-auto max-w-4xl"
/>

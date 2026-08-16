<script lang="ts">
  /**
   * The Tag Manager containers linked to a client, on the company detail page.
   *
   * Renders from stored rows only — the panel never waits for Google. A company page composes
   * every module's panel in sequence, so an integration that took three seconds here would take
   * three seconds off every client page load. What is *in* the container is one click away,
   * where waiting is the point rather than a surprise.
   */
  import { AlertTriangle, ExternalLink, Plus, Tags } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import GtmLinkDialog from "./GtmLinkDialog.svelte";

  interface PanelContainer {
    id: string;
    public_id: string;
    name: string;
    status: string;
    last_error?: string | null;
    live_version_id?: string | null;
    tag_count: number;
    workspace_changes: number;
    conversions: number;
    conversions_live: number;
    observed_at?: string | null;
    tag_manager_url: string;
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();
  const forbidden = $derived(Boolean(data.forbidden));
  const containers = $derived((data.containers ?? []) as PanelContainer[]);
  // Mirrors the key the *call* makes, not the one the panel is about (#310): linking posts to
  // `POST /gtm/containers`, which declares `google_tag_manager.settings.manage`. Gating this on
  // the panel's own read permission would draw a button the API then refuses.
  const canLink = $derived(Boolean(data.can_manage));

  let linking = $state(false);
</script>

{#if forbidden}
  <!-- Permission-gated: stay quiet rather than error the page. A card reading "no access" on a
       page full of working cards teaches nobody anything. -->
{:else if containers.length === 0}
  <p class="text-sm text-text-muted">{t("gtm.panel.empty")}</p>
  {#if canLink}
    <!-- It used to be a link to Instellingen → Tag Manager: an org-wide screen that dropped the
         client you were looking at and then asked you to hand-type a `GTM-XXXXXXX` off their
         website. Every other panel on this page keeps the client (`＋ Nieuwe website`, `＋ Nieuw
         domein`); this one now does too, and the id is searched for rather than typed. -->
    <button
      type="button"
      class="mt-2 inline-flex items-center gap-1 text-sm text-brand hover:underline"
      onclick={() => (linking = true)}
    >
      <Plus size={14} aria-hidden="true" />
      {t("gtm.panel.link_container")}
    </button>
  {/if}
{:else}
  <ul class="divide-y divide-border">
    {#each containers as container (container.id)}
      <li class="flex items-start gap-2 py-2">
        <Tags size={16} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <a
            href="/marketing/tag-manager/{container.id}"
            class="block truncate text-sm font-medium text-brand hover:underline"
          >
            {container.name}
          </a>
          <span class="mt-0.5 block truncate text-xs text-text-muted">
            {container.public_id}
            {#if container.live_version_id}
              <!-- A `_one` key pair and a ternary, not an ICU plural: this project's Paraglide
                   build does not parse `{n, plural, …}` and compiles it to the literal. -->
              · {container.tag_count === 1
                ? t("gtm.panel.tags_one")
                : t("gtm.panel.tags", { count: container.tag_count })}
            {:else}
              · {t("gtm.panel.no_live_version")}
            {/if}
            {#if container.conversions > 0}
              · {t("gtm.panel.conversions", {
                live: container.conversions_live,
                total: container.conversions,
              })}
            {/if}
          </span>
          {#if container.workspace_changes > 0}
            <!-- The number worth noticing without opening anything: a change staged weeks ago
                 and never published is the commonest way tracking quietly stops being what the
                 client was told it is. The glyph carries the state, not the colour alone —
                 brand gold reads as a warning on some tenants. -->
            <span class="mt-1 flex items-start gap-1.5 text-xs text-text">
              <AlertTriangle size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
              <span>
                {container.workspace_changes === 1
                  ? t("gtm.panel.staged_one")
                  : t("gtm.panel.staged", { count: container.workspace_changes })}
              </span>
            </span>
          {/if}
          {#if container.status === "error"}
            <!-- Google's own sentence, verbatim. It is the one thing that says *what* to fix. -->
            <span class="mt-1 flex items-start gap-1.5 text-xs text-text">
              <AlertTriangle size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
              <span class="min-w-0 break-words">{container.last_error ?? t("gtm.panel.error")}</span
              >
            </span>
          {:else if container.observed_at}
            <span class="mt-0.5 block text-xs text-text-muted">
              {t("gtm.panel.checked")}
              {fmtDateTime(container.observed_at)}
            </span>
          {/if}
        </div>
        <a
          href={container.tag_manager_url}
          target="_blank"
          rel="noreferrer"
          class="mt-0.5 shrink-0 text-text-muted hover:text-text"
          title={t("gtm.panel.open")}
        >
          <ExternalLink size={14} aria-hidden="true" />
          <span class="sr-only">{t("gtm.panel.open")}</span>
        </a>
      </li>
    {/each}
  </ul>
  {#if canLink}
    <!-- A client with one container often gets a second (a second brand, a server container),
         so the control stays after the first one — the shape every other panel's ＋ has. -->
    <button
      type="button"
      class="mt-2 inline-flex items-center gap-1 text-sm text-brand hover:underline"
      onclick={() => (linking = true)}
    >
      <Plus size={14} aria-hidden="true" />
      {t("gtm.panel.link_another")}
    </button>
  {/if}
{/if}

{#if canLink}
  <!-- The client is the route here, so the dialog asks only which container. Posts to the
       company page's own `?/gtmLink`, which `gtmActions` mounts. -->
  <GtmLinkDialog bind:open={linking} connectNext="/companies/{companyId}" />
{/if}

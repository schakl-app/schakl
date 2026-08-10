<script lang="ts">
  /**
   * The client-portal visibility marker: an eye when the client's portal contacts can see this
   * task, a struck-through eye when they cannot.
   *
   * `visible_to_client` was a checkbox on the card and nothing anywhere else, so the one fact a
   * staff member needs before writing in a task — "is a client reading this?" — was a click away
   * from every list. This is that fact, drawn the same way on every surface.
   *
   * Three rules live here rather than in each caller, because the marker rides the board, its
   * mobile row, the project to-do, the client panel and the card, and a rule remembered per
   * screen is a rule that drifts:
   *
   * - **A portal login never sees it.** Every task it can reach is visible to it by construction
   *   (`TaskService._PortalTaskRepository` ANDs `visible_to_client` into every read), so the
   *   marker would say the same thing on every row. A relevance gate, never the security one —
   *   the API is the boundary (CLAUDE.md §15).
   * - **"Hidden" is only drawn where there is an audience to be hidden from.** A task attached to
   *   no client is invisible-by-default and uninteresting; a struck eye on every internal row
   *   would be noise that teaches nothing.
   * - **"Visible" is always drawn, and a client-less one is drawn louder.** A task with no
   *   `company_id` is not out of the portal's reach: the company horizon reads a nullable link as
   *   "not company data" and lets it through (`TenantScopedRepository.horizon_condition`), so
   *   ticking one publishes it to *every* client with a portal login. That is exactly the row
   *   worth spotting from a list.
   */
  import { Eye, EyeOff } from "@lucide/svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let {
    visible,
    companyId = null,
    size = 14,
  }: {
    /** The task's `visible_to_client`. */
    visible: boolean;
    /** The task's `company_id`; absent means the task hangs off no client. */
    companyId?: string | null;
    size?: number;
  } = $props();

  const isPortal = $derived(page.data.user?.isPortal ?? false);
  // Every client, because nothing narrows it to one (see the note above).
  const allClients = $derived(visible && !companyId);
  const shown = $derived(!isPortal && (visible || !!companyId));

  const label = $derived(
    allClients
      ? t("tasks.client_visibility.all_clients")
      : visible
        ? t("tasks.client_visibility.visible")
        : t("tasks.client_visibility.hidden"),
  );
</script>

{#if shown}
  <!-- No `text-brand` here, on purpose. The brand colour is tenant data (Golden Rule 4) and this
       agency's is gold, so an amber warning next to a brand-coloured eye was two identical icons:
       a palette that only reads on some tenants is not a palette. The states separate on the
       glyph and on contrast instead — faint struck eye, solid eye, amber eye — and only the
       warning spends a hue. -->
  <span
    role="img"
    aria-label={label}
    title={label}
    class="inline-flex shrink-0 items-center {allClients
      ? 'text-amber-600 dark:text-amber-400'
      : visible
        ? 'text-text'
        : 'text-text-muted opacity-60'}"
  >
    {#if visible}
      <Eye {size} />
    {:else}
      <EyeOff {size} />
    {/if}
  </span>
{/if}

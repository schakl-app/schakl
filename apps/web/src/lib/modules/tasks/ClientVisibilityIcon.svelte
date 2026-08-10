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
   * - **"Visible" is always drawn, and a tick that reaches nobody is drawn louder.** A task ticked
   *   visible while hanging off neither a client nor a project is read by no one:
   *   `Task.__portal_horizon_clause__` anchors a client's reach on `company_id`, falling back to
   *   the *project's* client where the column is empty, and a task with neither is not any
   *   client's data. So the tick is inert, and a checkbox that silently does nothing is exactly
   *   the row worth spotting from a list. (It used to be the opposite hazard: the column-matched
   *   horizon read a nullable link as "not company data" and let such a task through to *every*
   *   portal login. Same marker, and now it warns about a promise unkept rather than one
   *   over-kept.)
   */
  import { Eye, EyeOff } from "@lucide/svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let {
    visible,
    companyId = null,
    projectId = null,
    size = 14,
  }: {
    /** The task's `visible_to_client`. */
    visible: boolean;
    /** The task's `company_id`; absent means the task hangs off no client directly. */
    companyId?: string | null;
    /** The task's `project_id` — the indirect anchor, and the reason absent ≠ unreachable. */
    projectId?: string | null;
    size?: number;
  } = $props();

  const isPortal = $derived(page.data.user?.isPortal ?? false);
  // Ticked, and anchored to nothing that could carry it to a client (see the note above).
  const noAudience = $derived(visible && !companyId && !projectId);
  const shown = $derived(!isPortal && (visible || !!companyId));

  const label = $derived(
    noAudience
      ? t("tasks.client_visibility.no_audience")
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
    class="inline-flex shrink-0 items-center {noAudience
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

<script lang="ts">
  /**
   * What happened to the folder somebody just asked for (#444).
   *
   * "Taakmap aanmaken" answers 202 and a worker does the work, so one optimistic reload was a
   * stopgap: a worker slower than ~5s (or a job that lands in the 5-minute sweep) left "de map
   * wordt aangemaakt…" standing forever, and a *failed* job was invisible to any screen. This
   * polls `/state` with backoff until the job leaves `pending`: gone → the folder landed, one
   * reload brings it in (the open browser is keyed on the folder id and walks into it);
   * `failed` → said out loud, with Google's own sentence when there is one. A dropped probe is
   * not a verdict (the Cloudflare rule) — the next attempt answers.
   */
  import { invalidateAll } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { entityType, entityId }: { entityType: string; entityId: string } = $props();

  const queued = $derived(Boolean(page.form?.driveProvisionQueued));
  let outcome = $state<"pending" | "landed" | "failed" | "stalled" | null>(null);
  let failureDetail = $state<string | null>(null);

  // Backoff, not a poll: seven probes over ~2 minutes, then the sweep is on its own.
  const DELAYS = [3000, 5000, 8000, 12000, 20000, 30000, 45000];

  $effect(() => {
    if (!queued) return;
    outcome = "pending";
    let cancelled = false;
    let step = 0;

    async function probe(): Promise<void> {
      if (cancelled) return;
      try {
        const response = await fetch(
          `/api/v1/google/drive/state?entity_type=${entityType}&entity_id=${entityId}`,
          { headers: { accept: "application/json" } },
        );
        if (response.ok) {
          const state = (await response.json()) as {
            job_status?: string | null;
            job_error?: string | null;
          };
          if (state.job_status === "failed") {
            outcome = "failed";
            failureDetail = state.job_error ?? null;
            return;
          }
          if (!state.job_status) {
            // The job left the queue: the folder exists, and the reload draws it.
            outcome = "landed";
            await invalidateAll();
            return;
          }
        }
      } catch {
        // Not a verdict; the next probe answers.
      }
      if (step < DELAYS.length) {
        setTimeout(() => void probe(), DELAYS[step++]);
      } else {
        outcome = "stalled";
      }
    }

    setTimeout(() => void probe(), DELAYS[step++]);
    return () => {
      cancelled = true;
    };
  });
</script>

{#if queued && outcome === "pending"}
  <p class="mt-2 text-sm text-text-muted" role="status">{t("google.drive.folder_queued")}</p>
{:else if outcome === "failed"}
  <p class="mt-2 text-sm text-red-600 dark:text-red-400" role="status">
    {t("google.drive.folder_failed")}{#if failureDetail}{" "}({failureDetail}){/if}
  </p>
{:else if outcome === "stalled"}
  <p class="mt-2 text-sm text-text-muted" role="status">{t("google.drive.folder_slow")}</p>
{/if}

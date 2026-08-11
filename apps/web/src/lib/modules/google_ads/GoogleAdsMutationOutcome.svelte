<script lang="ts">
  /**
   * What a write actually did — per operation, and per operation the policy refused.
   *
   * Two lists rather than one, because "we did not ask" and "Google said no" are different
   * sentences and only the second is fixable in Google's own interface. Collapsing them is how a
   * screen ends up telling somebody to go and fix a permission when the answer is on the policy
   * page they can reach in two clicks.
   *
   * Business-licensed — see LICENSE.
   */
  import { AlertTriangle, Check, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  interface Result {
    index: number;
    ok: boolean;
    resource_name?: string | null;
    error_code?: string | null;
    message?: string | null;
  }
  interface Skipped {
    subject: string;
    reason: string;
    blocks?: string | null;
    limit?: number | null;
  }
  interface Outcome {
    resource: string;
    validate_only: boolean;
    requested: number;
    applied: number;
    results?: Result[];
    skipped?: Skipped[];
    warnings?: string[];
  }

  let { outcome }: { outcome: Outcome } = $props();

  const failed = $derived((outcome.results ?? []).filter((r) => !r.ok));
  const skipped = $derived(outcome.skipped ?? []);
  const warnings = $derived(outcome.warnings ?? []);
</script>

<section class="mb-3 rounded-xl border border-border bg-surface-raised p-3">
  <p class="flex items-center gap-1.5 text-sm text-text">
    {#if outcome.applied > 0}
      <Check size={14} aria-hidden="true" />
    {:else}
      <AlertTriangle size={14} aria-hidden="true" />
    {/if}
    {t("google_ads.outcome.applied", {
      applied: outcome.applied,
      requested: outcome.requested,
    })}
  </p>

  {#each warnings as warning (warning)}
    <p class="mt-1 text-xs text-text-muted">{t(warning)}</p>
  {/each}

  {#if skipped.length > 0}
    <ul class="mt-2 space-y-1">
      {#each skipped as item (item.subject)}
        <li class="text-xs text-text">
          <span class="font-medium">{item.subject}</span> ·
          <!-- Our refusal, so it is translated — and it names the protected term it would have
               blocked, because "refused" invites an argument and "would also block *beugel*"
               invites a fix. -->
          {t(item.reason)}
          {#if item.blocks}
            · {t("google_ads.outcome.blocks", { term: item.blocks })}
          {/if}
          {#if item.limit != null}
            · {item.limit}
          {/if}
        </li>
      {/each}
    </ul>
  {/if}

  {#if failed.length > 0}
    <ul class="mt-2 space-y-1">
      {#each failed as item (item.index)}
        <li class="flex items-start gap-1.5 text-xs text-text-muted">
          <X size={12} class="mt-0.5 shrink-0" aria-hidden="true" />
          <!-- Google's own sentence, untranslated on purpose: it is provider text, and a
               translator would have to invent a Dutch word for `KEYWORD_HAS_TOO_MANY_WORDS`. -->
          <span>{item.message ?? item.error_code ?? ""}</span>
        </li>
      {/each}
    </ul>
  {/if}
</section>

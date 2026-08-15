<script lang="ts">
  /**
   * Where the app's toasts are drawn (#364) — mounted once, in the (app) shell.
   *
   * Bottom-centre on mobile and bottom-right above it: out of the thumb's way on a phone, out of
   * the reading column on a laptop, and never over the header controls the user is about to
   * reach for next. `aria-live="polite"` rather than `assertive` — a save confirmation must not
   * interrupt a screen reader mid-sentence, which is precisely what it would do to someone who
   * has already moved on.
   */
  import { Check, Info, TriangleAlert, X } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  import { dismissToast, toasts } from "./toast.svelte";

  const items = $derived(toasts());

  // Tone is carried by a glyph *and* a colour, never colour alone: the tenant brand is gold on
  // some orgs, which renders identically to an amber warning (the app's own hard-won rule).
  const ICON = { success: Check, info: Info, error: TriangleAlert } as const;
  const TONE = {
    success:
      "border-green-600/30 bg-green-50 text-green-900 dark:border-green-400/30 dark:bg-green-950 dark:text-green-200",
    info: "border-border bg-surface-raised text-text",
    error:
      "border-red-600/30 bg-red-50 text-red-900 dark:border-red-400/30 dark:bg-red-950 dark:text-red-200",
  } as const;
</script>

<div
  class="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:items-end"
  aria-live="polite"
  aria-atomic="false"
>
  {#each items as toast (toast.id)}
    {@const Icon = ICON[toast.tone]}
    <div
      class="pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded-xl border px-3 py-2.5 text-sm shadow-lg {TONE[
        toast.tone
      ]}"
      role={toast.tone === "error" ? "alert" : "status"}
    >
      <Icon size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
      <span class="min-w-0 flex-1">{toast.message}</span>
      {#if toast.undo}
        <button
          type="button"
          class="shrink-0 font-medium underline underline-offset-2"
          onclick={() => {
            toast.undo?.();
            dismissToast(toast.id);
          }}
        >
          {t("common.undo")}
        </button>
      {/if}
      <button
        type="button"
        class="shrink-0 opacity-60 hover:opacity-100"
        aria-label={t("common.close")}
        onclick={() => dismissToast(toast.id)}
      >
        <X size={14} />
      </button>
    </div>
  {/each}
</div>

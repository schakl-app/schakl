<script lang="ts">
  /**
   * Manually glue this email onto another's conversation (#272) — for a reply Gmail didn't
   * thread automatically (a different-address sender, a forwarded copy). A debounced search over
   * *your own* logged gmail emails (the API scopes it the same way, harder); pick the email whose
   * conversation this one should join, then merge.
   *
   * Same family as `InteractionMoveDialog` / `CloseTaskDialog`: a form body the host wraps in a
   * `<Modal>`, posting to `?/addInteractionToConversation`. Results load on search, never on open
   * (docs/PERFORMANCE.md) — it reuses the list endpoint's own `q`, no bounded preload.
   */
  import { enhance } from "$app/forms";
  import type { SubmitFunction } from "@sveltejs/kit";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  import type { InteractionItem } from "./format";
  import { snippetPreview } from "./snippet";

  let {
    interaction,
    onsaved,
  }: {
    interaction: InteractionItem;
    onsaved?: () => void;
  } = $props();

  let q = $state("");
  let results = $state<InteractionItem[]>([]);
  let loading = $state(false);
  let selected = $state<InteractionItem | null>(null);
  let error = $state("");

  let timer: ReturnType<typeof setTimeout> | undefined;
  let seq = 0;
  function onInput(value: string) {
    q = value;
    selected = null;
    clearTimeout(timer);
    timer = setTimeout(() => void search(), 300);
  }

  async function search() {
    const query = q.trim();
    if (!query) {
      results = [];
      loading = false;
      return;
    }
    loading = true;
    const mine = ++seq;
    const params = new URLSearchParams({
      kind: "email",
      status: "logged",
      mine: "true",
      q: query,
      limit: "8",
    });
    const response = await fetch(`/api/v1/interactions?${params}`, {
      headers: { accept: "application/json" },
    });
    if (mine !== seq) return; // a later keystroke already superseded this fetch
    const page = response.ok ? await response.json() : { items: [] };
    // Never offer to fold a row onto itself.
    results = (page.items ?? []).filter((row: InteractionItem) => row.id !== interaction.id);
    loading = false;
  }

  const busy = new InFlight();
  const submit: SubmitFunction = (input) =>
    busy.wrap("merge", () => async ({ result, update }) => {
      if (result.type === "failure") {
        error = String(result.data?.error ?? "errors.validation");
        return;
      }
      error = "";
      await update({ reset: false });
      onsaved?.();
    })(input);
</script>

<form method="POST" action="?/addInteractionToConversation" class="space-y-4" use:enhance={submit}>
  <input type="hidden" name="id" value={interaction.id} />
  <input type="hidden" name="target_interaction_id" value={selected?.id ?? ""} />

  <p class="text-sm text-text-muted">{t("interactions.add_to_conversation_help")}</p>

  <label class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.add_to_conversation")}</span>
    <input
      type="text"
      value={q}
      oninput={(e) => onInput(e.currentTarget.value)}
      placeholder={t("interactions.add_to_conversation_search")}
      class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
      autocomplete="off"
    />
  </label>

  {#if loading}
    <p class="text-sm text-text-muted">{t("common.loading")}</p>
  {:else if q.trim() && results.length === 0}
    <p class="text-sm text-text-muted">{t("interactions.add_to_conversation_empty")}</p>
  {:else if results.length > 0}
    <ul class="max-h-64 divide-y divide-border overflow-y-auto rounded-lg border border-border">
      {#each results as row (row.id)}
        <li>
          <button
            type="button"
            onclick={() => (selected = row)}
            aria-pressed={selected?.id === row.id}
            class="flex w-full flex-col gap-0.5 px-3 py-2 text-left hover:bg-surface {selected?.id ===
            row.id
              ? 'bg-brand/10 ring-1 ring-inset ring-brand/30'
              : ''}"
          >
            <span class="truncate text-sm font-medium text-text">
              {row.subject || t("interactions.kind.email")}
            </span>
            <span class="truncate text-xs text-text-muted">
              {fmtDateTime(row.occurred_at)}{#if row.owner_name}&nbsp;· {row.owner_name}{/if}
            </span>
            {#if row.snippet}
              <span class="truncate text-xs text-text-muted">{snippetPreview(row.snippet, 80)}</span
              >
            {/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end gap-2">
    <Button type="submit" loading={busy.is("merge")} disabled={!selected || busy.active}>
      {t("interactions.add_to_conversation_confirm")}
    </Button>
  </div>
</form>

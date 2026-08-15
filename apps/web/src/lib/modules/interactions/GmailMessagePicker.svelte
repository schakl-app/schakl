<script lang="ts">
  /**
   * The messages a Gmail reference resolved to, one of which is about to become a
   * contactmoment (#342).
   *
   * One component for both ways in, because they answer the same question with the same list:
   * a pasted link, and "mist er een bericht?" on a conversation we already logged part of.
   * What differs is only where the id came from.
   *
   * Two states are drawn rather than hidden. **Already logged** keeps the message visible,
   * marked, instead of offering a button that could only ever answer 409 — the whole point of
   * the list is telling you which of these are missing, and a gap you cannot see the edges of
   * is not a gap. It deliberately carries **no link to the row**: there is no by-id route to a
   * contactmoment, and the nearest thing (a subject search) is precisely wrong here, where
   * every message in the thread shares one subject. **Rejected earlier** says so out loud:
   * importing anyway is allowed
   * (it is the reader's own earlier decision, and this is them changing it), but a screen that
   * quietly overrides a standing instruction makes the instruction untrustworthy.
   */
  import { Check, Mail } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import type { components } from "$lib/core/api/schema";

  type Candidate = components["schemas"]["GmailCandidate"];

  let {
    messages = [],
    truncated = false,
    selected = "",
    onpick,
  }: {
    messages?: Candidate[];
    truncated?: boolean;
    /** The `message_id` currently chosen, if any. */
    selected?: string;
    onpick: (message: Candidate) => void;
  } = $props();

  const sender = (m: Candidate) => m.from_name || m.from_email || "—";
</script>

{#if messages.length === 0}
  <p class="text-sm text-text-muted">{t("interactions.gmail.none")}</p>
{:else}
  <ul class="space-y-2">
    {#each messages as message (message.message_id)}
      {@const picked = selected === message.message_id}
      <li
        class="rounded-lg border p-3 text-sm {picked ? 'border-brand bg-surface' : 'border-border'}"
      >
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0">
            <p class="truncate font-medium text-text">
              {message.subject || t("interactions.eml.message")}
            </p>
            <p class="truncate text-xs text-text-muted">
              {sender(message)}{message.recipients ? ` → ${message.recipients}` : ""}
            </p>
            {#if message.occurred_at}
              <p class="text-xs text-text-muted">{fmtDateTime(message.occurred_at)}</p>
            {/if}
            {#if message.snippet}
              <p class="mt-1 line-clamp-2 text-xs text-text-muted">{message.snippet}</p>
            {/if}
          </div>
          <div class="flex shrink-0 flex-col items-end gap-1">
            {#if message.logged}
              <!-- A glyph, not a colour: the tenant's brand may be any hue, so state that
                   reads only as "gold vs amber" reads as nothing (docs/UX.md). -->
              <span class="inline-flex items-center gap-1 text-xs text-text-muted">
                <Check size={13} aria-hidden="true" />
                {t("interactions.gmail.already_logged")}
              </span>
            {:else}
              <button
                type="button"
                onclick={() => onpick(message)}
                class="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium {picked
                  ? 'border-brand text-brand'
                  : 'border-border text-text hover:border-brand hover:text-brand'}"
              >
                {#if picked}
                  <Check size={13} aria-hidden="true" />
                  {t("interactions.gmail.picked")}
                {:else}
                  <Mail size={13} aria-hidden="true" />
                  {t("interactions.gmail.pick")}
                {/if}
              </button>
            {/if}
          </div>
        </div>
        {#if message.suppressed}
          <p class="mt-2 text-xs text-amber-700 dark:text-amber-400">
            {t("interactions.gmail.suppressed")}
          </p>
        {/if}
      </li>
    {/each}
  </ul>
  {#if truncated}
    <p class="mt-2 text-xs text-text-muted">{t("interactions.gmail.truncated")}</p>
  {/if}
{/if}

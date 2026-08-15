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
   *
   * **And every row that is not here says why** (#372). The reason comes from the API, which
   * takes it from the ingest's own decision function rather than from a second opinion, so the
   * chip and the behaviour cannot drift apart. It is drawn as information, not as a fault:
   * none of the ten reasons is the reader's mistake, most are the integration working exactly
   * as configured, and an amber warning over "this sender is not a contact yet" is the shape
   * CLAUDE.md §10 already names — a state you exist to serve, rendered as a problem. So: one
   * muted line with an ⓘ, beside the button that resolves it.
   *
   * The reasons are deliberately **not** colour-coded by severity, for the reason docs/UX.md
   * gives about brand colour — the tenant's hue may be anything, so a state told apart only by
   * colour is a state told apart by nothing.
   */
  import { Check, Info, Mail } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { hasMessage, t } from "$lib/core/i18n";
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

  /**
   * The one line under a message that says why it is not on the timeline.
   *
   * The order mirrors the API's and matters: `before_connection` and `never_offered` beat any
   * gate verdict, because for those messages the chain never ran. Printing "the sender is not a
   * contact" about a mail from before this mailbox was connected would be true of the rules and
   * false about what happened — the confident-and-wrong answer the whole design exists to
   * avoid. An unknown reason (an API newer than this bundle) degrades to nothing rather than
   * printing a raw message key at somebody.
   */
  function reasonFor(m: Candidate): string | null {
    if (m.logged) return null;
    if (m.before_connection) return t("interactions.gmail.skip.before_connection");
    if (m.never_offered) return t("interactions.gmail.skip.never_offered");
    if (!m.skip_reason) return null;
    const key = `interactions.gmail.skip.${m.skip_reason}`;
    return hasMessage(key) ? t(key, m.skip_detail ?? {}) : null;
  }
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
        {#if reasonFor(message)}
          <p class="mt-2 flex items-start gap-1.5 text-xs text-text-muted">
            <Info size={13} class="mt-px shrink-0" aria-hidden="true" />
            <span>{reasonFor(message)}</span>
          </p>
        {/if}
      </li>
    {/each}
  </ul>
  {#if truncated}
    <p class="mt-2 text-xs text-text-muted">{t("interactions.gmail.truncated")}</p>
  {/if}
{/if}

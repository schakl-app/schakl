<script lang="ts">
  /**
   * The channels a post goes to, as a row of toggles: an avatar, a name, which network.
   *
   * A channel that cannot be published to is **drawn and disabled, with its reason under it**
   * — never hidden. A Page missing from the row reads as "we do not have that client's Page",
   * while the truth ("its token expired") is one a person can do something about.
   *
   * Posts its picks as repeated `asset_ids` fields, so a plain form carries them.
   */
  import { t } from "$lib/core/i18n";
  import Avatar from "$lib/core/ui/Avatar.svelte";

  import ChannelMark from "./ChannelMark.svelte";
  import type { ChannelOption } from "./types";

  let {
    channels,
    selected = $bindable([]),
    name = "asset_ids",
    formId,
    disabled = false,
    onchange,
  }: {
    channels: ChannelOption[];
    selected?: string[];
    name?: string;
    formId?: string;
    disabled?: boolean;
    onchange?: (selected: string[]) => void;
  } = $props();

  function toggle(id: string): void {
    selected = selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id];
    onchange?.(selected);
  }
</script>

<div class="flex flex-wrap gap-2" role="group" aria-label={t("meta.composer.channels")}>
  {#each channels as channel (channel.id)}
    {@const on = selected.includes(channel.id)}
    {@const blocked = !channel.canPublish}
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled || (blocked && !on)}
      onclick={() => toggle(channel.id)}
      class="flex min-w-0 max-w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left text-sm transition
        {on
        ? 'border-brand bg-brand/5 text-text ring-1 ring-brand'
        : 'border-border bg-surface-raised text-text-muted hover:border-text-muted'}
        disabled:cursor-not-allowed disabled:opacity-60"
    >
      <Avatar name={channel.name} avatarUrl={channel.pictureUrl} size="sm" />
      <span class="min-w-0">
        <span class="flex items-center gap-1.5">
          <span class="truncate font-medium text-text">{channel.name}</span>
          <ChannelMark channel={channel.channel} size={14} class="text-text-muted" />
        </span>
        {#if blocked && channel.blockedBy}
          <span class="block truncate text-xs text-text-muted"
            >{t(channel.blockedBy, { name: channel.name })}</span
          >
        {:else if channel.username}
          <span class="block truncate text-xs text-text-muted">@{channel.username}</span>
        {/if}
      </span>
    </button>
    {#if on}
      <input type="hidden" {name} value={channel.id} form={formId} />
    {/if}
  {/each}
</div>

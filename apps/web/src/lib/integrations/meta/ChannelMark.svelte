<script lang="ts">
  /**
   * Which channel something is on, as a glyph. Drawn here because the icon set carries no
   * vendor marks — and drawn in `currentColor`, so it takes the text colour of wherever it
   * sits rather than bringing a brand colour onto a screen whose colours mean states
   * (docs/UX.md §1).
   *
   * Never colour alone and never glyph alone: the channel's name rides as the accessible label,
   * and as visible text wherever `label` is set.
   */
  import { channelLabel } from "./format";

  let {
    channel,
    size = 16,
    label = false,
    class: extra = "",
  }: {
    channel: string;
    size?: number;
    /** Print the channel's name beside the glyph. */
    label?: boolean;
    class?: string;
  } = $props();

  const name = $derived(channelLabel(channel));
</script>

<span class="inline-flex shrink-0 items-center gap-1.5 {extra}" title={label ? undefined : name}>
  {#if channel === "instagram"}
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      role="img"
      aria-label={label ? undefined : name}
      aria-hidden={label ? "true" : undefined}
    >
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.5" cy="6.5" r="0.6" fill="currentColor" />
    </svg>
  {:else}
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      role="img"
      aria-label={label ? undefined : name}
      aria-hidden={label ? "true" : undefined}
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M15 8h-1.5A2.5 2.5 0 0 0 11 10.5V21M9 13h5" />
    </svg>
  {/if}
  {#if label}<span>{name}</span>{/if}
</span>

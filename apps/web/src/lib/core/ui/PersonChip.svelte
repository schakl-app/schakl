<script lang="ts">
  /**
   * One person, one shape: the {@link Avatar} disc *and* the name, together, always.
   *
   * A surface that shows several people shows them all this way. Naming the first one in full
   * and degrading the rest to bare discs reads as two different things in one row — the reason
   * this chip exists at all — so a list that runs out of room caps the number of people it
   * shows (`Assignees`) rather than stripping the names off the ones past the first.
   */
  import Avatar from "$lib/core/ui/Avatar.svelte";

  let {
    name = null,
    email = null,
    avatarUrl = null,
    label = null,
    size = "sm",
    muted = false,
    title = null,
  }: {
    name?: string | null;
    email?: string | null;
    avatarUrl?: string | null;
    /**
     * What to write next to the disc, when that isn't the name — "Onbekende medewerker" for a
     * user the caller could not resolve. The disc still falls back to its own `?`, because
     * initials made from a placeholder would read as a real person's.
     */
    label?: string | null;
    /** sm = 24px (rows, table cells) · md = 32px (headers). */
    size?: "xs" | "sm" | "md";
    /** Secondary people (everyone but the verantwoordelijke) carry the muted name colour. */
    muted?: boolean;
    title?: string | null;
  } = $props();

  const text = $derived(label || name || email || "");
</script>

<span class="inline-flex min-w-0 items-center gap-1.5 align-middle" title={title ?? text}>
  <Avatar {name} {email} {avatarUrl} {size} title={title ?? text} />
  <span class="truncate {muted ? 'text-text-muted' : 'text-text'}">{text}</span>
</span>

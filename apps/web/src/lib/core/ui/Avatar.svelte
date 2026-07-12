<script lang="ts" module>
  /**
   * The one initials rule (#122): first letter of the first two words, split on whitespace,
   * @, dots, underscores and dashes — four hand-rolled copies had drifted into disagreeing
   * regexes, so the rule now lives here and nowhere else.
   */
  export function initials(name: string | null | undefined, email?: string | null): string {
    const source = (name || email || "").trim();
    const parts = source.split(/[\s@._-]+/).filter(Boolean);
    return (parts[0]?.[0] ?? "?").concat(parts[1]?.[0] ?? "").toUpperCase();
  }
</script>

<script lang="ts">
  /**
   * A person, everywhere (#122): the effective avatar image when one is known, the initials
   * disc otherwise. Sizes are the discs the app already draws; list images stay small and
   * lazy (docs/PERFORMANCE.md). A broken image URL falls back to initials rather than the
   * browser's broken-image glyph.
   */
  let {
    name = null,
    email = null,
    avatarUrl = null,
    size = "md",
    ring = false,
    title = null,
  }: {
    name?: string | null;
    email?: string | null;
    avatarUrl?: string | null;
    /** xs = 16px (mention chips) · sm = 24px (stacks, rows) · md = 32px (header, lists). */
    size?: "xs" | "sm" | "md";
    /** ring-2 in the stack's surface colour, for overlapping stacks. */
    ring?: boolean;
    title?: string | null;
  } = $props();

  let broken = $state(false);

  const sizeClass = $derived(
    {
      xs: "h-4 w-4 text-[8px]",
      sm: "h-6 w-6 text-[10px]",
      md: "h-8 w-8 text-xs",
    }[size],
  );
</script>

{#if avatarUrl && !broken}
  <img
    src={avatarUrl}
    alt={name || email || ""}
    title={title ?? (name || email || undefined)}
    loading="lazy"
    onerror={() => (broken = true)}
    class="inline-block {sizeClass} rounded-full object-cover {ring
      ? 'ring-2 ring-surface-raised'
      : ''}"
  />
{:else}
  <span
    title={title ?? (name || email || undefined)}
    class="inline-flex {sizeClass} items-center justify-center rounded-full bg-surface font-medium text-text-muted {ring
      ? 'ring-2 ring-surface-raised'
      : ''}"
  >
    {initials(name, email)}
  </span>
{/if}

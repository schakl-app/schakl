<script lang="ts">
  /**
   * The dashboard tile chrome (#166): the card + title row every My Day widget wears.
   *
   * Since #404 it is a thin wrapper over `Card`, which owns the four kinds and the heading —
   * and the `kind` prop is the point of the rewrite. Thirteen tiles were one object: "Uren
   * vandaag" (one figure) and "Openstaande taken per project" (twelve rows) were the same
   * white box under the same 14 px heading, so the reader had to parse each one to find out
   * what kind of thing it was. A widget that leads with a figure says `kind="stat"` and gets
   * the borderless tinted tile; a widget that leads with a list keeps the panel.
   *
   * A widget still composes this rather than re-typing the classes (docs/UX.md, the widget
   * convention) — five of the seventeen widgets were drawing their own chrome and are now here.
   *
   * **`linkLabel` is not where a total goes** (#407). This card offered a header link and no
   * slot for a truncation notice, so the two widgets that were honest about their cut smuggled
   * the number into the link's own text — "Alle 23 beoordelen" — while five others said nothing
   * at all. A widget that draws rows wraps them in `PanelRows`, which owns the expander, the
   * hand-over and the count, and puts all three under the rows they are about. The header link
   * stays what it always was: "this tile's module", not "this tile's remainder".
   */
  import type { Snippet } from "svelte";

  import Card from "$lib/core/ui/Card.svelte";

  let {
    title,
    kind = "panel",
    href,
    linkLabel,
    children,
  }: {
    title: string;
    /** `stat` for a tile whose first line is a figure; `panel` for a list or a form. */
    kind?: "stat" | "panel";
    /** Optional "show all" link in the header, e.g. the module's own list page. */
    href?: string;
    linkLabel?: string;
    children: Snippet;
  } = $props();
</script>

<Card {kind} {title} {href} {linkLabel}>{@render children()}</Card>

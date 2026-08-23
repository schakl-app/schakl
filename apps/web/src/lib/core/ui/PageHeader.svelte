<script lang="ts">
  /**
   * The title band (#404) — the one shape every screen opens with.
   *
   * Breadcrumb → **title band** → optional vital signs → content. The breadcrumb is already
   * app-wide (the `(app)` layout draws it from the route), and the content is whatever the
   * screen is; the band is the part that had been re-invented per page. Measured before this
   * existed: `/tasks` opened with tabs, then a title, then filter chips, then a toolbar; the
   * dashboard had a title and an edit button in a bespoke flex row; the client hub had a
   * header of its own with the status pill, the assignees and a five-item action bar. Three
   * screens the team lives in, three shapes, and two of them 20 px H1s against one 18 px.
   *
   * They should differ in what they *contain*, not in their shape. So: title left with
   * whatever belongs beside it (`beside` — a status pill, a client's second name), the
   * subtitle under it, actions right, and the whole thing wrapping to a stack on a phone.
   *
   * Deliberately *not* here: breadcrumbs (the layout's), the vital-signs strip
   * (`SummaryStrip`, contributed per record), tabs and filters (they belong to the content,
   * and putting them in the band is what made `/tasks` a four-storey opening). A band that
   * grows a `tabs` prop is a band that has stopped being one shape.
   */
  import type { Snippet } from "svelte";

  import { PAGE_TITLE } from "$lib/core/ui/headings";

  let {
    title,
    subtitle,
    leading,
    beside,
    actions,
    class: extra = "mb-6",
  }: {
    title: string;
    /** One line under the title. A welcome, a count, a warning about the list below. */
    subtitle?: Snippet;
    /** An identity mark in front of the title: a client's logo, a record's avatar. */
    leading?: Snippet;
    /** On the title's own line: a status pill, a marker, a second name. */
    beside?: Snippet;
    /** Right-aligned controls. Wrap under the title on a phone rather than squeezing it. */
    actions?: Snippet;
    class?: string;
  } = $props();
</script>

<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-3 {extra}">
  <div class="min-w-0 flex-1">
    <div class="flex min-w-0 flex-wrap items-center gap-3">
      {#if leading}{@render leading()}{/if}
      <h1 class="{PAGE_TITLE} min-w-0 break-words">{title}</h1>
      {#if beside}{@render beside()}{/if}
    </div>
    {#if subtitle}
      <div class="mt-1 text-sm text-text-muted">{@render subtitle()}</div>
    {/if}
  </div>
  {#if actions}
    <!-- `min-w-0`, never `shrink-0`: the wrap is what keeps a phone from scrolling sideways, and
       `shrink-0` is what stops it happening — the box stays at its content width (the client hub's
       five actions measured 485 px), so the flex line breaks and then overflows anyway. The title
       is `flex-1` with a zero basis, so it is not the thing that gets squeezed in exchange. -->
    <div class="flex min-w-0 flex-wrap items-center gap-2">{@render actions()}</div>
  {/if}
</div>

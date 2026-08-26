<script lang="ts">
  /**
   * Donut for shares of one measure. Two colourings, and which one a slice gets is the
   * caller's claim to make:
   *
   * - **Rank** (the default): a single-hue sequential ramp by magnitude — identity lives in
   *   the legend. What the revenue page has always drawn (top clients by omzet).
   * - **State**: a slice carrying `state` is painted from the state palette (#404) — a
   *   budgets donut colours by burn, where an over-budget slice is a claim, never a shade of
   *   blue. Never the tenant's brand. The caller still owes the palette's other half ("never
   *   colour alone"): a glyph-bearing reading of the same fact beside the chart.
   *
   * `format` says the unit (#437): this chart was born on the revenue page and hard-coded
   * euros, so hours rendered as money on any second caller. Slices and legend figures may
   * carry links (`href` opens the record, `valueHref` opens the records behind the figure) —
   * a figure the reader cannot take apart is a figure they will not trust (UX Principle 7).
   */
  import { fmtMoney } from "$lib/core/format";
  import { stateFillClass, stateSvgFillClass, type UiState } from "$lib/core/state";
  import { resolvedTheme } from "$lib/core/theme-mode.svelte";

  interface Slice {
    label: string;
    value: number;
    /** Painted from the state palette instead of the ramp when set. */
    state?: UiState;
    /** Where the slice (and its legend name) opens. */
    href?: string;
    /** Where the legend figure opens — the records behind the number. */
    valueHref?: string;
  }

  let {
    slices,
    otherLabel,
    otherValue = 0,
    centerLabel,
    format = fmtMoney,
  }: {
    slices: Slice[];
    otherLabel: string;
    otherValue?: number;
    centerLabel: string;
    /** How a value prints — the caller's unit. Defaults to euros (the first caller's). */
    format?: (value: number) => string;
  } = $props();

  // Sequential blue ramp, dark→light with rank (monotonic lightness). Two variants — the light
  // one's dark end (#1e40af) is validated for a white card but reads as near-invisible on a
  // dark surface, so dark mode gets its own brighter range, re-validated against the dark
  // surface with dataviz's ordinal checks rather than just lightened by eye (issue #14).
  const RAMP_LIGHT_MODE = { dark: [30, 64, 175], light: [147, 197, 253] }; // #1e40af -> #93c5fd
  const RAMP_DARK_MODE = { dark: [59, 130, 246], light: [191, 219, 254] }; // #3b82f6 -> #bfdbfe
  // Neutral "other" bucket: validated as >=3:1 against both the light and dark surface as-is.
  const OTHER_COLOR = "#a8a29e";

  function rampColor(index: number, count: number): string {
    const ramp = resolvedTheme.current === "dark" ? RAMP_DARK_MODE : RAMP_LIGHT_MODE;
    const f = count <= 1 ? 0 : index / (count - 1);
    const channel = (i: number) => Math.round(ramp.dark[i] + (ramp.light[i] - ramp.dark[i]) * f);
    return `rgb(${channel(0)},${channel(1)},${channel(2)})`;
  }

  const all = $derived(
    otherValue > 0 ? [...slices, { label: otherLabel, value: otherValue }] : [...slices],
  );
  const total = $derived(all.reduce((sum, s) => sum + s.value, 0));

  const R = 80;
  const INNER = 48;
  const CX = 100;
  const CY = 100;

  function arcPath(startFraction: number, endFraction: number): string {
    const a0 = startFraction * 2 * Math.PI - Math.PI / 2;
    const a1 = endFraction * 2 * Math.PI - Math.PI / 2;
    const large = endFraction - startFraction > 0.5 ? 1 : 0;
    const x0 = CX + R * Math.cos(a0);
    const y0 = CY + R * Math.sin(a0);
    const x1 = CX + R * Math.cos(a1);
    const y1 = CY + R * Math.sin(a1);
    const ix1 = CX + INNER * Math.cos(a1);
    const iy1 = CY + INNER * Math.sin(a1);
    const ix0 = CX + INNER * Math.cos(a0);
    const iy0 = CY + INNER * Math.sin(a0);
    return `M ${x0} ${y0} A ${R} ${R} 0 ${large} 1 ${x1} ${y1} L ${ix1} ${iy1} A ${INNER} ${INNER} 0 ${large} 0 ${ix0} ${iy0} Z`;
  }

  const arcs = $derived.by(() => {
    let acc = 0;
    return all.map((slice: Slice, i) => {
      const start = total > 0 ? acc / total : 0;
      acc += slice.value;
      const end = total > 0 ? acc / total : 0;
      const isOther = otherValue > 0 && i === all.length - 1;
      return {
        ...slice,
        path: arcPath(start, end),
        // A stated state wins; rank ramps only the stateless slices' world (the first caller).
        color: isOther ? OTHER_COLOR : slice.state ? null : rampColor(i, slices.length),
        fillClass: !isOther && slice.state ? stateSvgFillClass(slice.state) : "",
        swatchClass: !isOther && slice.state ? stateFillClass(slice.state) : "",
        share: total > 0 ? slice.value / total : 0,
      };
    });
  });

  let hovered = $state<number | null>(null);
</script>

<div class="flex flex-wrap items-center gap-6">
  <svg viewBox="0 0 200 200" class="h-44 w-44 shrink-0" role="img">
    {#each arcs as arc, i (arc.label)}
      {#snippet slicePath()}
        <path
          d={arc.path}
          fill={arc.color ?? undefined}
          class="stroke-surface-raised {arc.fillClass}"
          stroke-width="2"
          opacity={hovered === null || hovered === i ? 1 : 0.4}
          onmouseenter={() => (hovered = i)}
          onmouseleave={() => (hovered = null)}
          role="presentation"
        />
      {/snippet}
      {#if arc.href}
        <a href={arc.href} aria-label={arc.label}>{@render slicePath()}</a>
      {:else}
        {@render slicePath()}
      {/if}
    {/each}
    <text
      x={CX}
      y={CY - 4}
      text-anchor="middle"
      class="fill-text text-[15px] font-semibold tabular-nums"
    >
      {format(hovered !== null ? arcs[hovered].value : total)}
    </text>
    <text x={CX} y={CY + 13} text-anchor="middle" class="fill-text-muted text-[9px]">
      {hovered !== null ? arcs[hovered].label.slice(0, 22) : centerLabel}
    </text>
  </svg>

  <!-- The legend is the identity + table view: name, amount, share. -->
  <ul class="min-w-0 flex-1 space-y-1">
    {#each arcs as arc, i (arc.label)}
      <li
        class="flex items-center gap-2 rounded px-1.5 py-0.5 text-sm {hovered === i
          ? 'bg-surface'
          : ''}"
        onmouseenter={() => (hovered = i)}
        onmouseleave={() => (hovered = null)}
      >
        <span
          class="h-2.5 w-2.5 shrink-0 rounded-sm {arc.swatchClass}"
          style={arc.color ? `background:${arc.color}` : ""}
        ></span>
        {#if arc.href}
          <a href={arc.href} class="min-w-0 flex-1 truncate text-text hover:text-brand">
            {arc.label}
          </a>
        {:else}
          <span class="min-w-0 flex-1 truncate text-text">{arc.label}</span>
        {/if}
        {#if arc.valueHref}
          <a
            href={arc.valueHref}
            class="shrink-0 tabular-nums text-text-muted underline-offset-2 hover:text-brand hover:underline"
            >{format(arc.value)}</a
          >
        {:else}
          <span class="shrink-0 tabular-nums text-text-muted">{format(arc.value)}</span>
        {/if}
        <span class="w-11 shrink-0 text-right text-xs tabular-nums text-text-muted">
          {(arc.share * 100).toFixed(1)}%
        </span>
      </li>
    {/each}
  </ul>
</div>

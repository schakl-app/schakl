<script lang="ts">
  /**
   * Donut for ranked shares of one measure (top clients by omzet). Slices use a single-hue
   * sequential ramp by rank (magnitude, not identity — identity lives in the legend), with
   * 2px surface gaps between slices; the trailing "other" bucket is neutral.
   */
  import { fmtMoney } from "$lib/core/format";
  import { resolvedTheme } from "$lib/core/theme-mode.svelte";

  interface Slice {
    label: string;
    value: number;
  }

  let {
    slices,
    otherLabel,
    otherValue = 0,
    centerLabel,
  }: {
    slices: Slice[];
    otherLabel: string;
    otherValue?: number;
    centerLabel: string;
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
    return all.map((slice, i) => {
      const start = total > 0 ? acc / total : 0;
      acc += slice.value;
      const end = total > 0 ? acc / total : 0;
      const isOther = otherValue > 0 && i === all.length - 1;
      return {
        ...slice,
        path: arcPath(start, end),
        color: isOther ? OTHER_COLOR : rampColor(i, slices.length),
        share: total > 0 ? slice.value / total : 0,
      };
    });
  });

  let hovered = $state<number | null>(null);
</script>

<div class="flex flex-wrap items-center gap-6">
  <svg viewBox="0 0 200 200" class="h-44 w-44 shrink-0" role="img">
    {#each arcs as arc, i (arc.label)}
      <path
        d={arc.path}
        fill={arc.color}
        class="stroke-surface-raised"
        stroke-width="2"
        opacity={hovered === null || hovered === i ? 1 : 0.4}
        onmouseenter={() => (hovered = i)}
        onmouseleave={() => (hovered = null)}
        role="presentation"
      />
    {/each}
    <text
      x={CX}
      y={CY - 4}
      text-anchor="middle"
      class="fill-text text-[15px] font-semibold tabular-nums"
    >
      {fmtMoney(hovered !== null ? arcs[hovered].value : total)}
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
        <span class="h-2.5 w-2.5 shrink-0 rounded-sm" style="background:{arc.color}"></span>
        <span class="min-w-0 flex-1 truncate text-text">{arc.label}</span>
        <span class="shrink-0 tabular-nums text-text-muted">{fmtMoney(arc.value)}</span>
        <span class="w-11 shrink-0 text-right text-xs tabular-nums text-text-muted">
          {(arc.share * 100).toFixed(1)}%
        </span>
      </li>
    {/each}
  </ul>
</div>

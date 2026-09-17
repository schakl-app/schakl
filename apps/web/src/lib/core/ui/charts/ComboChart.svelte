<script lang="ts">
  /**
   * Bars on the left axis, a line on the right: cost per day beside conversions per day, the
   * one chart on an advertising page where two units share a time axis and neither may be
   * read against the other's scale.
   *
   * Hand-rolled inline SVG like `TrendChart`, measured rather than fixed (`geometry.ts`): the
   * box is the container's width at 1 user unit = 1 CSS px, the design height is a constant.
   * Colours are dataviz-validated hexes re-validated for dark, never the tenant brand
   * (docs/UX.md). The bar is a proportion of its slot, never a fixed width (`barWidth`).
   */
  import { fmtDayMonth } from "$lib/core/format";
  import { resolvedTheme } from "$lib/core/theme-mode.svelte";

  import { barWidth, chartWidth } from "./geometry";

  let {
    dates,
    bars,
    line,
    barLabel,
    lineLabel,
    formatBar,
    formatLine,
  }: {
    dates: string[];
    bars: number[];
    line: number[];
    barLabel: string;
    lineLabel: string;
    formatBar: (v: number) => string;
    formatLine: (v: number) => string;
  } = $props();

  const barColor = $derived(resolvedTheme.current === "dark" ? "#60a5fa" : "#93c5fd");
  const lineColor = $derived(resolvedTheme.current === "dark" ? "#f59e0b" : "#d97706");

  let box = $state(0);
  const W = $derived(chartWidth(box, 720, 280));
  const H = 220;
  const PAD = { top: 12, right: 56, bottom: 22, left: 56 };
  const plotW = $derived(W - PAD.left - PAD.right);
  const plotH = H - PAD.top - PAD.bottom;

  function niceTop(max: number): number {
    if (max <= 0) return 1;
    const step = Math.pow(10, Math.floor(Math.log10(max)));
    return Math.max(Math.ceil(max / step) * step, 1);
  }
  const barTop = $derived(niceTop(Math.max(...bars, 0)));
  const lineTop = $derived(niceTop(Math.max(...line, 0)));
  const n = $derived(Math.max(dates.length, 1));
  const slot = $derived(plotW / n);
  const bw = $derived(barWidth(slot, 6, 2, 0.6));

  const xCenter = (i: number) => PAD.left + slot * i + slot / 2;
  const yBar = $derived((v: number) => PAD.top + plotH - (v / barTop) * plotH);
  const yLine = $derived((v: number) => PAD.top + plotH - (v / lineTop) * plotH);
  const linePoints = $derived(
    line.map((v, i) => `${xCenter(i).toFixed(1)},${yLine(v).toFixed(1)}`).join(" "),
  );

  let hover = $state<number | null>(null);
  function onmove(event: MouseEvent) {
    if (dates.length === 0) return;
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    hover = Math.max(0, Math.min(n - 1, Math.floor((px - PAD.left) / slot)));
  }
</script>

<figure class="relative" bind:clientWidth={box}>
  <svg
    viewBox="0 0 {W} {H}"
    class="w-full"
    height={H}
    role="img"
    aria-label="{barLabel} / {lineLabel}"
    onmousemove={onmove}
    onmouseleave={() => (hover = null)}
  >
    {#each [0, 0.5, 1] as f (f)}
      <line
        x1={PAD.left}
        x2={W - PAD.right}
        y1={PAD.top + plotH - f * plotH}
        y2={PAD.top + plotH - f * plotH}
        class="stroke-border"
        stroke-width="1"
      />
      <text
        x={PAD.left - 8}
        y={PAD.top + plotH - f * plotH + 3}
        text-anchor="end"
        class="fill-text-muted text-[10px] tabular-nums"
      >
        {formatBar(barTop * f)}
      </text>
      <text
        x={W - PAD.right + 8}
        y={PAD.top + plotH - f * plotH + 3}
        text-anchor="start"
        class="fill-text-muted text-[10px] tabular-nums"
      >
        {formatLine(lineTop * f)}
      </text>
    {/each}
    {#each bars as v, i (i)}
      <rect
        x={xCenter(i) - bw / 2}
        y={yBar(v)}
        width={bw}
        height={Math.max(0, PAD.top + plotH - yBar(v))}
        fill={barColor}
        opacity={hover === null || hover === i ? 1 : 0.5}
        rx="1"
      />
    {/each}
    {#if line.length > 1}
      <polyline
        points={linePoints}
        fill="none"
        stroke={lineColor}
        stroke-width="2"
        stroke-linejoin="round"
      />
    {/if}
    {#each line as v, i (i)}
      <circle cx={xCenter(i)} cy={yLine(v)} r={hover === i ? 4 : 2.5} fill={lineColor} />
    {/each}
    {#if dates.length}
      <text x={PAD.left} y={H - 6} text-anchor="start" class="fill-text-muted text-[10px]">
        {fmtDayMonth(dates[0])}
      </text>
      <text x={W - PAD.right} y={H - 6} text-anchor="end" class="fill-text-muted text-[10px]">
        {fmtDayMonth(dates[dates.length - 1])}
      </text>
    {/if}
  </svg>
  {#if hover !== null && dates[hover]}
    <div
      class="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-xs shadow-lg"
      style="left: {(xCenter(hover) / W) * 100}%; top: {(PAD.top / H) * 100}%"
    >
      <p class="text-text-muted">{fmtDayMonth(dates[hover])}</p>
      <p class="font-semibold text-text tabular-nums">{formatBar(bars[hover] ?? 0)}</p>
      <p class="text-text tabular-nums">{formatLine(line[hover] ?? 0)}</p>
    </div>
  {/if}
  <figcaption class="mt-1 flex flex-wrap gap-4 text-xs text-text-muted">
    <span class="flex items-center gap-1.5">
      <span class="inline-block h-2.5 w-2.5 rounded-sm" style="background:{barColor}"></span>
      {barLabel}
    </span>
    <span class="flex items-center gap-1.5">
      <span class="inline-block h-0.5 w-3" style="background:{lineColor}"></span>
      {lineLabel}
    </span>
  </figcaption>
</figure>

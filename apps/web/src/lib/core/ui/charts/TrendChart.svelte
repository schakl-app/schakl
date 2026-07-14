<script lang="ts">
  /**
   * A single-series area/line trend over a date range (issue #134's marketing tab).
   *
   * Hand-rolled inline SVG like the rest of the app's charts: the series colour is a
   * dataviz-validated hex (never the tenant brand, docs/UX.md), re-validated for dark; gridlines,
   * axis text and the tooltip chrome use semantic tokens so they follow the theme. The caller
   * passes a `format` fn so the y-axis and tooltip label the metric correctly (money/percent/count).
   */
  import { fmtDayMonth } from "$lib/core/format";
  import { resolvedTheme } from "$lib/core/theme-mode.svelte";

  let {
    dates,
    values,
    label,
    format,
  }: {
    dates: string[];
    values: number[];
    label: string;
    format: (v: number) => string;
  } = $props();

  const color = $derived(resolvedTheme.current === "dark" ? "#3b82f6" : "#2563eb");

  const W = 720;
  const H = 200;
  const PAD = { top: 12, right: 12, bottom: 22, left: 52 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const max = $derived(Math.max(...values, 1));
  const step = $derived(Math.pow(10, Math.floor(Math.log10(max || 1))));
  const top = $derived(Math.max(Math.ceil(max / step) * step, 1));
  const ticks = $derived([0, top / 2, top]);

  const x = (i: number) => PAD.left + (values.length <= 1 ? 0 : (i / (values.length - 1)) * plotW);
  const y = $derived((v: number) => PAD.top + plotH - (v / top) * plotH);

  const line = $derived(
    values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
  );
  const area = $derived(
    values.length
      ? `M ${x(0).toFixed(1)},${(PAD.top + plotH).toFixed(1)} ` +
          values.map((v, i) => `L ${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ") +
          ` L ${x(values.length - 1).toFixed(1)},${(PAD.top + plotH).toFixed(1)} Z`
      : "",
  );

  let hover = $state<{ x: number; y: number; i: number } | null>(null);
  function onmove(event: MouseEvent) {
    if (values.length === 0) return;
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    const i = Math.max(
      0,
      Math.min(values.length - 1, Math.round(((px - PAD.left) / plotW) * (values.length - 1))),
    );
    hover = { x: x(i), y: y(values[i]), i };
  }
</script>

<figure class="relative">
  <svg
    viewBox="0 0 {W} {H}"
    class="w-full"
    role="img"
    aria-label={label}
    onmousemove={onmove}
    onmouseleave={() => (hover = null)}
  >
    {#each ticks as tick (tick)}
      <line
        x1={PAD.left}
        x2={W - PAD.right}
        y1={y(tick)}
        y2={y(tick)}
        class="stroke-border"
        stroke-width="1"
      />
      <text x={PAD.left - 8} y={y(tick) + 3} text-anchor="end" class="fill-text-muted text-[10px] tabular-nums">
        {format(tick)}
      </text>
    {/each}

    {#if values.length}
      <path d={area} fill={color} opacity="0.12" />
      <polyline points={line} fill="none" stroke={color} stroke-width="2" stroke-linejoin="round" />
      {#if dates.length}
        <text x={PAD.left} y={H - 6} text-anchor="start" class="fill-text-muted text-[10px]">
          {fmtDayMonth(dates[0])}
        </text>
        <text x={W - PAD.right} y={H - 6} text-anchor="end" class="fill-text-muted text-[10px]">
          {fmtDayMonth(dates[dates.length - 1])}
        </text>
      {/if}
      {#if hover}
        <line x1={hover.x} x2={hover.x} y1={PAD.top} y2={PAD.top + plotH} class="stroke-border" stroke-width="1" />
        <circle cx={hover.x} cy={hover.y} r="3" fill={color} />
      {/if}
    {/if}
  </svg>

  {#if hover}
    <div
      class="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-xs shadow-lg"
      style="left: {(hover.x / W) * 100}%; top: {(hover.y / H) * 100}%"
    >
      <p class="font-semibold text-text tabular-nums">{format(values[hover.i])}</p>
      <p class="text-text-muted">{fmtDayMonth(dates[hover.i])}</p>
    </div>
  {/if}
</figure>

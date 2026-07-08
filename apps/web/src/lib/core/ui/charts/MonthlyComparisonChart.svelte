<script lang="ts">
  /**
   * Grouped monthly bars: the selected year vs the previous year on one € axis.
   * Palette validated (dataviz six checks): current #2563eb, previous #d97706.
   */
  import { fmtMoney, monthLabels } from "$lib/core/format";

  let {
    current,
    previous,
    currentLabel,
    previousLabel,
  }: {
    current: number[];
    previous: number[];
    currentLabel: string;
    previousLabel: string;
  } = $props();

  const CURRENT_COLOR = "#2563eb";
  const PREVIOUS_COLOR = "#d97706";

  const W = 760;
  const H = 240;
  const PAD = { top: 12, right: 8, bottom: 24, left: 56 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const months = monthLabels();
  const max = $derived(Math.max(...current, ...previous, 1));
  // Round the axis top up to a tidy step.
  const step = $derived(Math.pow(10, Math.floor(Math.log10(max))));
  const top = $derived(Math.ceil(max / step) * step);
  const ticks = $derived([0, top / 2, top]);

  const groupW = plotW / 12;
  const barW = Math.min(14, (groupW - 8) / 2);

  const y = $derived((v: number) => PAD.top + plotH - (v / top) * plotH);
  const barH = $derived((v: number) => Math.max(v > 0 ? 2 : 0, (v / top) * plotH));

  let tooltip = $state<{ x: number; y: number; month: string; cur: number; prev: number } | null>(
    null,
  );
  function showTooltip(event: MouseEvent, index: number) {
    const host = (event.currentTarget as SVGElement).closest("figure");
    const rect = host?.getBoundingClientRect();
    tooltip = {
      x: event.clientX - (rect?.left ?? 0),
      y: event.clientY - (rect?.top ?? 0) - 8,
      month: months[index],
      cur: current[index],
      prev: previous[index],
    };
  }
</script>

<figure class="relative">
  <svg viewBox="0 0 {W} {H}" class="w-full" role="img">
    <!-- recessive gridlines + € tick labels -->
    {#each ticks as tick (tick)}
      <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)}
        stroke="#f5f5f5" stroke-width="1" />
      <text x={PAD.left - 8} y={y(tick) + 3} text-anchor="end"
        class="fill-neutral-400 text-[10px] tabular-nums">{fmtMoney(tick)}</text>
    {/each}

    {#each months as month, i (month)}
      {@const gx = PAD.left + i * groupW + (groupW - barW * 2 - 2) / 2}
      <!-- oversized hover target per month group -->
      <rect x={PAD.left + i * groupW} y={PAD.top} width={groupW} height={plotH}
        fill="transparent"
        onmousemove={(e) => showTooltip(e, i)}
        onmouseleave={() => (tooltip = null)}
        role="presentation" />
      <rect x={gx} y={y(previous[i])} width={barW} height={barH(previous[i])}
        rx="2" fill={PREVIOUS_COLOR} pointer-events="none" />
      <rect x={gx + barW + 2} y={y(current[i])} width={barW} height={barH(current[i])}
        rx="2" fill={CURRENT_COLOR} pointer-events="none" />
      <text x={PAD.left + i * groupW + groupW / 2} y={H - 8} text-anchor="middle"
        class="fill-neutral-400 text-[10px]">{month}</text>
    {/each}
  </svg>

  {#if tooltip}
    <div
      class="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-xs shadow-lg"
      style="left: {tooltip.x}px; top: {tooltip.y}px"
    >
      <p class="mb-1 font-semibold capitalize text-neutral-900">{tooltip.month}</p>
      <p class="flex items-center gap-1.5 tabular-nums text-neutral-700">
        <span class="h-2 w-2 rounded-full" style="background:{CURRENT_COLOR}"></span>
        {currentLabel}: {fmtMoney(tooltip.cur)}
      </p>
      <p class="flex items-center gap-1.5 tabular-nums text-neutral-700">
        <span class="h-2 w-2 rounded-full" style="background:{PREVIOUS_COLOR}"></span>
        {previousLabel}: {fmtMoney(tooltip.prev)}
      </p>
    </div>
  {/if}

  <figcaption class="mt-2 flex items-center gap-4 text-xs text-neutral-600">
    <span class="flex items-center gap-1.5">
      <span class="h-2.5 w-2.5 rounded-sm" style="background:{CURRENT_COLOR}"></span>
      {currentLabel}
    </span>
    <span class="flex items-center gap-1.5">
      <span class="h-2.5 w-2.5 rounded-sm" style="background:{PREVIOUS_COLOR}"></span>
      {previousLabel}
    </span>
  </figcaption>
</figure>

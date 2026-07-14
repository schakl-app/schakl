<script lang="ts">
  /**
   * A tiny inline-SVG trend line for a panel KPI — no axis, no labels, just the shape (#134).
   *
   * The series colour is a dataviz-validated hex (NOT the tenant brand colour, docs/UX.md), with
   * a separate value re-validated for the dark surface; structure needs no semantic token because
   * a sparkline draws nothing but the line and its faint fill.
   */
  import { resolvedTheme } from "$lib/core/theme-mode.svelte";

  let {
    values,
    tone = "up",
    height = 28,
    width = 96,
  }: {
    values: number[];
    /** "up" reads positive/blue, "down" reads negative/amber — matches the KPI's delta tone. */
    tone?: "up" | "down" | "flat";
    height?: number;
    width?: number;
  } = $props();

  const COLORS = {
    up: { light: "#2563eb", dark: "#3b82f6" },
    down: { light: "#d97706", dark: "#f59e0b" },
    flat: { light: "#64748b", dark: "#94a3b8" },
  };
  const color = $derived(COLORS[tone][resolvedTheme.current === "dark" ? "dark" : "light"]);

  const pad = 2;
  const plotW = $derived(width - pad * 2);
  const plotH = $derived(height - pad * 2);
  const max = $derived(Math.max(...values, 1));
  const min = $derived(Math.min(...values, 0));
  const span = $derived(max - min || 1);

  const points = $derived(
    values.length <= 1
      ? []
      : values.map((v, i) => {
          const x = pad + (i / (values.length - 1)) * plotW;
          const y = pad + plotH - ((v - min) / span) * plotH;
          return [x, y] as [number, number];
        }),
  );
  const line = $derived(points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "));
  const area = $derived(
    points.length
      ? `M ${points[0][0].toFixed(1)},${(height - pad).toFixed(1)} ` +
          points.map(([x, y]) => `L ${x.toFixed(1)},${y.toFixed(1)}`).join(" ") +
          ` L ${points[points.length - 1][0].toFixed(1)},${(height - pad).toFixed(1)} Z`
      : "",
  );
</script>

{#if points.length}
  <svg
    viewBox="0 0 {width} {height}"
    width={width}
    height={height}
    role="img"
    aria-hidden="true"
    class="overflow-visible"
  >
    <path d={area} fill={color} opacity="0.12" />
    <polyline
      points={line}
      fill="none"
      stroke={color}
      stroke-width="1.5"
      stroke-linejoin="round"
      stroke-linecap="round"
    />
  </svg>
{/if}

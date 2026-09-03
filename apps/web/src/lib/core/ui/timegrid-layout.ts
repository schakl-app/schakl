/**
 * The time grid's geometry, lifted out of `TimeGrid.svelte` so a second surface that draws
 * hours as pixels — the scheduling dialog's conflict columns — packs concurrent blocks the same
 * way the Agenda does, rather than growing a second opinion about what "side by side" means.
 *
 * Instants in, org-local minutes out (§8): the browser only ever converts *from* an instant *to*
 * the org's wall clock, never the other way.
 */
import { getTimeZone } from "$lib/core/timezone";

/** An instant's local calendar day + minute-of-day, in the org zone (§8). */
export function localParts(iso: string): { day: string; minutes: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: getTimeZone(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    day: `${get("year")}-${get("month")}-${get("day")}`,
    minutes: Number(get("hour")) * 60 + Number(get("minute")),
  };
}

/**
 * Clip an instant span to one local day's column: minutes from midnight, or `null` when the
 * span never touches that day. A span that started yesterday begins at 00:00 here; one that ends
 * tomorrow runs to 24:00.
 */
export function clipToDay(
  startsAt: string,
  endsAt: string,
  day: string,
): { startMin: number; endMin: number } | null {
  const start = localParts(startsAt);
  const end = localParts(endsAt);
  const startMin = start.day === day ? start.minutes : start.day < day ? 0 : null;
  const endMin = end.day === day ? end.minutes : end.day > day ? 24 * 60 : null;
  if (startMin === null || endMin === null || endMin <= 0) return null;
  return { startMin, endMin };
}

export interface Lane {
  startMin: number;
  endMin: number;
  lane: number;
  lanes: number;
}

/**
 * Greedy lane assignment per overlap cluster, so concurrent blocks sit side by side. Mutates
 * `lane`/`lanes` on the items and returns them sorted by start; a 5-minute block is stretched to
 * twenty so it still has room for a label.
 */
export function packLanes<T extends Lane>(items: T[]): T[] {
  const blocks = [...items].sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
  for (const block of blocks) block.endMin = Math.max(block.endMin, block.startMin + 20);
  const laneEnds: number[] = [];
  let cluster: T[] = [];
  let clusterEnd = -1;
  for (const block of blocks) {
    if (block.startMin >= clusterEnd && cluster.length) {
      for (const done of cluster) done.lanes = laneEnds.length;
      cluster = [];
      laneEnds.length = 0;
    }
    let lane = laneEnds.findIndex((end) => end <= block.startMin);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(0);
    }
    laneEnds[lane] = block.endMin;
    block.lane = lane;
    cluster.push(block);
    clusterEnd = Math.max(clusterEnd, block.endMin);
  }
  for (const done of cluster) done.lanes = laneEnds.length;
  return blocks;
}

/**
 * The shared label palette: color tokens stored by the API (task labels, leave types) map
 * to static Tailwind classes here (JIT needs the literal class names in source). Core so
 * every module and the shared calendar use the same tokens without cross-module imports.
 */

export const LABEL_COLORS = [
  "red",
  "orange",
  "amber",
  "green",
  "emerald",
  "teal",
  "sky",
  "blue",
  "violet",
  "pink",
] as const;

export type LabelColor = (typeof LABEL_COLORS)[number];

const CHIP: Record<string, string> = {
  red: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  orange: "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  green: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  emerald: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  teal: "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300",
  sky: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  violet: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
  pink: "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300",
};

const DOT: Record<string, string> = {
  red: "bg-red-500 dark:bg-red-400",
  orange: "bg-orange-500 dark:bg-orange-400",
  amber: "bg-amber-500 dark:bg-amber-400",
  green: "bg-green-500 dark:bg-green-400",
  emerald: "bg-emerald-500 dark:bg-emerald-400",
  teal: "bg-teal-500 dark:bg-teal-400",
  sky: "bg-sky-500 dark:bg-sky-400",
  blue: "bg-blue-500 dark:bg-blue-400",
  violet: "bg-violet-500 dark:bg-violet-400",
  pink: "bg-pink-500 dark:bg-pink-400",
};

export function labelChipClass(color: string): string {
  return CHIP[color] ?? "bg-surface text-text-muted";
}

export function labelDotClass(color: string): string {
  return DOT[color] ?? "bg-text-muted";
}

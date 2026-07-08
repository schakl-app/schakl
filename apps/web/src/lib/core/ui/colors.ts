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
  red: "bg-red-100 text-red-700",
  orange: "bg-orange-100 text-orange-700",
  amber: "bg-amber-100 text-amber-800",
  green: "bg-green-100 text-green-700",
  emerald: "bg-emerald-100 text-emerald-700",
  teal: "bg-teal-100 text-teal-700",
  sky: "bg-sky-100 text-sky-700",
  blue: "bg-blue-100 text-blue-700",
  violet: "bg-violet-100 text-violet-700",
  pink: "bg-pink-100 text-pink-700",
};

const DOT: Record<string, string> = {
  red: "bg-red-500",
  orange: "bg-orange-500",
  amber: "bg-amber-500",
  green: "bg-green-500",
  emerald: "bg-emerald-500",
  teal: "bg-teal-500",
  sky: "bg-sky-500",
  blue: "bg-blue-500",
  violet: "bg-violet-500",
  pink: "bg-pink-500",
};

export function labelChipClass(color: string): string {
  return CHIP[color] ?? "bg-neutral-100 text-neutral-600";
}

export function labelDotClass(color: string): string {
  return DOT[color] ?? "bg-neutral-400";
}

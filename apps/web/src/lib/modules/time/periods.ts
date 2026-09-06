/**
 * The period presets the employees report offers — rolling, so a bookmarked "vorige maand"
 * keeps meaning last month next month (#316's rule for the marketing dashboard, one report
 * over). Pure date arithmetic over a `YYYY-MM-DD` the caller resolved on the tenant's own
 * calendar (`orgToday()`, §8); nothing here reads a clock.
 */
export const EMPLOYEE_PERIODS = ["month", "last_month", "quarter", "year"] as const;
export type EmployeePeriod = (typeof EMPLOYEE_PERIODS)[number];

const pad = (n: number) => String(n).padStart(2, "0");
const lastDay = (year: number, month: number) => new Date(Date.UTC(year, month, 0)).getUTCDate();

/** Both ends of a preset. A to-date preset ends on `today`; last month is the whole month. */
export function presetRange(preset: EmployeePeriod, today: string): [string, string] {
  const year = Number(today.slice(0, 4));
  const month = Number(today.slice(5, 7));
  switch (preset) {
    case "last_month": {
      const y = month === 1 ? year - 1 : year;
      const m = month === 1 ? 12 : month - 1;
      return [`${y}-${pad(m)}-01`, `${y}-${pad(m)}-${pad(lastDay(y, m))}`];
    }
    case "quarter": {
      const first = Math.floor((month - 1) / 3) * 3 + 1;
      return [`${year}-${pad(first)}-01`, today];
    }
    case "year":
      return [`${year}-01-01`, today];
    case "month":
    default:
      return [`${year}-${pad(month)}-01`, today];
  }
}

export function isEmployeePeriod(value: string | null | undefined): value is EmployeePeriod {
  return (EMPLOYEE_PERIODS as readonly string[]).includes(value ?? "");
}

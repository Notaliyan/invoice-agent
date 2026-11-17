/** Number and date formatting shared by both pages. */

export function money(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return "—";
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
    }).format(value);
  } catch {
    // An unknown currency code makes Intl throw; show the code as it came.
    return `${value.toFixed(2)} ${currency}`;
  }
}

/** Axis-sized money: 7,260 → "7.3K". Whole numbers stay whole under 10,000. */
export function compact(value: number): string {
  if (Math.abs(value) < 10_000) {
    return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(value);
  }
  return new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return `${(ms / 1000).toFixed(1)} s`;
}

/** "2024-03-14" → "14 Mar 2024". Parsed by hand: no time zone surprises. */
export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return iso;
  return `${day} ${MONTHS[month - 1] ?? "?"} ${year}`;
}

/** "2024-03" → "Mar 2024", or "Mar" when the year would repeat. */
export function monthLabel(yyyymm: string, withYear = true): string {
  const [year, month] = yyyymm.split("-").map(Number);
  const name = MONTHS[(month ?? 1) - 1] ?? yyyymm;
  return withYear ? `${name} ${year}` : name;
}

/** "2026-11-16" → "16 Nov". */
export function dayLabel(iso: string): string {
  const [, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${day} ${MONTHS[(month ?? 1) - 1] ?? "?"}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Round tick values covering [min, max]: 0, 2K, 4K rather than 0, 1,816, 3,633.
 * Returns the ticks and the padded domain they span.
 */
export function niceTicks(min: number, max: number, target = 4): number[] {
  if (max <= min) return [min, min + 1];
  const rawStep = (max - min) / target;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const residual = rawStep / magnitude;
  const step = (residual >= 5 ? 10 : residual >= 2 ? 5 : residual >= 1 ? 2 : 1) * magnitude;
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let value = start; value <= end + step / 2; value += step) {
    ticks.push(Number(value.toPrecision(12)));
  }
  return ticks;
}

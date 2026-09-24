/**
 * Display formatting only. Values arrive fully computed from the backend; these
 * helpers never derive new numbers (spec §111).
 */
const LOCALE = "en-US";
const EMPTY = "—";

const compactFormatter = new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 });
const numberFormatter = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 });
const dateTimeFormatter = new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short" });
const relativeFormatter = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** 8200 → "8.2K", 2_400_000 → "2.4M". */
export function formatCompact(value: number | null | undefined): string {
  return isFiniteNumber(value) ? compactFormatter.format(value) : EMPTY;
}

/** 1234567.891 → "1,234,567.89". */
export function formatNumber(value: number | null | undefined, maximumFractionDigits = 2): string {
  if (!isFiniteNumber(value)) return EMPTY;
  return maximumFractionDigits === 2
    ? numberFormatter.format(value)
    : new Intl.NumberFormat(LOCALE, { maximumFractionDigits }).format(value);
}

/** A 0..1 ratio: 0.82 → "82%", 0.9995 with 2 digits → "99.95%". */
export function formatPercent(ratio: number | null | undefined, maximumFractionDigits = 0): string {
  if (!isFiniteNumber(ratio)) return EMPTY;
  return new Intl.NumberFormat(LOCALE, { style: "percent", maximumFractionDigits }).format(ratio);
}

/** 247 → "$247", 12.5 → "$12.50". */
export function formatCurrency(value: number | null | undefined, currency = "USD"): string {
  if (!isFiniteNumber(value)) return EMPTY;
  const fractionDigits = Number.isInteger(value) ? 0 : 2;
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value);
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/** Binary multiples: 1536 → "1.5 KB". */
export function formatBytes(bytes: number | null | undefined): string {
  if (!isFiniteNumber(bytes) || bytes < 0) return EMPTY;
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${formatNumber(value, unit === 0 ? 0 : 1)} ${BYTE_UNITS[unit]}`;
}

/** 8200, "/s" → "8.2K/s". */
export function formatRate(value: number | null | undefined, unit: string): string {
  return isFiniteNumber(value) ? `${formatCompact(value)}${unit}` : EMPTY;
}

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "Sep 23, 2026, 9:55 PM" in the viewer's time zone. */
export function formatDateTime(iso: string | null | undefined): string {
  const date = parseDate(iso);
  return date ? dateTimeFormatter.format(date) : EMPTY;
}

const RELATIVE_STEPS: ReadonlyArray<[Intl.RelativeTimeFormatUnit, number]> = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 30],
  ["month", 12],
  ["year", Number.POSITIVE_INFINITY],
];

/** "just now", "5 minutes ago", "yesterday", "in 2 hours". */
export function formatRelativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  const date = parseDate(iso);
  if (!date) return EMPTY;
  let delta = (date.getTime() - now.getTime()) / 1000;
  if (Math.abs(delta) < 45) return "just now";
  for (const [unit, size] of RELATIVE_STEPS) {
    if (Math.abs(delta) < size) return relativeFormatter.format(Math.round(delta), unit);
    delta /= size;
  }
  return formatDateTime(iso);
}

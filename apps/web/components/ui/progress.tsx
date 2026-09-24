import { cn } from "@/lib/utils";

export type MeterTone = "accent" | "warning" | "danger" | "info" | "neutral";

const FILL: Record<MeterTone, string> = {
  accent: "bg-accent-strong",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
  neutral: "bg-control",
};

export interface MeterProps {
  /** 0..1 */
  value: number;
  label: string;
  tone?: MeterTone;
  className?: string;
  /** Optional warning threshold marker, 0..1 */
  threshold?: number;
}

/** A utilisation bar. Uses role="meter": it measures a value in a range, not task progress. */
export function Meter({ value, label, tone = "accent", threshold, className }: MeterProps) {
  const clamped = Math.min(Math.max(value, 0), 1);
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-surface-2", className)}
    >
      <div className={cn("h-full rounded-full", FILL[tone])} style={{ width: `${clamped * 100}%` }} />
      {threshold !== undefined ? (
        <div
          aria-hidden
          className="absolute inset-y-0 w-px bg-fg/40"
          style={{ left: `${threshold * 100}%` }}
        />
      ) : null}
    </div>
  );
}

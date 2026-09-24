import { cn } from "@/lib/utils";

export interface MetricCardProps {
  label: string;
  /** Already formatted, e.g. "2.4M". */
  value: string;
  unit?: string;
  description?: string;
  className?: string;
}

/** One backend-computed metric. Rendered as a dt/dd pair, so place it inside a <dl>. */
export function MetricCard({ label, value, unit, description, className }: MetricCardProps) {
  return (
    <div
      className={cn("flex flex-col gap-1 rounded-md border border-default bg-surface px-4 py-3", className)}
    >
      <dt className="label-caps">{label}</dt>
      <dd className="flex items-baseline gap-1.5">
        <span className="tabular text-2xl font-semibold text-fg">{value}</span>
        {unit ? <span className="text-xs text-muted">{unit}</span> : null}
      </dd>
      {description ? <dd className="text-xs text-fg-secondary">{description}</dd> : null}
    </div>
  );
}

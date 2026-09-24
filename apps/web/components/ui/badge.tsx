import { AlertTriangle, CheckCircle2, CircleDashed, OctagonAlert } from "lucide-react";

import { cn } from "@/lib/utils";

export type BadgeTone = "neutral" | "accent" | "warning" | "danger" | "info";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-2 text-fg-secondary border-default",
  accent: "bg-accent-soft text-accent-fg border-accent/40",
  warning: "bg-warning-soft text-warning-fg border-warning/40",
  danger: "bg-danger-soft text-danger-fg border-danger/40",
  info: "bg-info-soft text-info-fg border-info/40",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

/** Pills communicate status, tags and filters only (spec §17). */
export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-full border px-2 text-2xs font-medium whitespace-nowrap",
        "[&_svg]:size-3",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

export type HealthStatus = "healthy" | "warning" | "critical" | "unknown";

export const STATUS_META: Record<
  HealthStatus,
  { label: string; tone: BadgeTone; Icon: typeof CheckCircle2 }
> = {
  healthy: { label: "Healthy", tone: "accent", Icon: CheckCircle2 },
  warning: { label: "Warning", tone: "warning", Icon: AlertTriangle },
  critical: { label: "Critical", tone: "danger", Icon: OctagonAlert },
  unknown: { label: "Not analyzed", tone: "neutral", Icon: CircleDashed },
};

/** Icon + text + colour, never colour alone (spec §62). */
export function StatusBadge({
  status,
  label,
  className,
}: {
  status: HealthStatus;
  label?: string;
  className?: string;
}) {
  const meta = STATUS_META[status];
  return (
    <Badge tone={meta.tone} className={className}>
      <meta.Icon aria-hidden />
      {label ?? meta.label}
    </Badge>
  );
}

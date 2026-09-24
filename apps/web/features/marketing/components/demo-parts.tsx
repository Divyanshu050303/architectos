/** Presentational pieces of the landing-page ArchitectureDemo: diagram connectors and node state. */
import { ArrowDown, ArrowRight } from "lucide-react";

import { formatCompact, formatPercent } from "@/lib/formatting";
import { cn } from "@/lib/utils";

import { DAU_STEPS, type DemoComponent } from "../demo-model";

/** Whole requests per second, compacted: 185.2 → "185", 18 518 → "18.5K". */
export function rate(value: number): string {
  return formatCompact(Math.round(value));
}

export function dauIndex(dau: number): number {
  const index = DAU_STEPS.findIndex((step) => step === dau);
  return index === -1 ? 0 : index;
}

export function nodeState(c: DemoComponent, bottleneck: string | null, resource: string) {
  if (!c.enabled) {
    return { status: "disabled" as const, statusLabel: "Off", metric: null, utilization: null, badges: [] };
  }
  return {
    status: c.status,
    statusLabel: c.status === "healthy" ? "Healthy" : c.status === "warning" ? "Warning" : "Over capacity",
    metric: { label: resource, value: formatPercent(c.utilization) },
    utilization: c.utilization,
    badges:
      bottleneck === c.id
        ? [
            {
              label: "Bottleneck",
              tone: c.status === "critical" ? ("danger" as const) : ("warning" as const),
            },
          ]
        : [],
  };
}

/** Arrow between two nodes. "flow" is vertical on small screens and horizontal from lg. */
export function Connector({
  label,
  flow,
  off = false,
  className,
}: {
  label: string;
  flow: boolean;
  off?: boolean;
  className?: string;
}) {
  const line = off ? "border-dashed border-strong" : "border-control";
  return (
    <div
      aria-hidden
      className={cn(
        "flex items-center justify-center gap-2 py-1 text-muted",
        flow && "lg:w-16 lg:flex-col lg:gap-1 lg:px-1 lg:py-0",
        className,
      )}
    >
      <span className={cn("flex h-6 flex-col items-center", flow && "lg:hidden")}>
        <span className={cn("w-0 flex-1 border-l", line)} />
        <ArrowDown className="-mt-1 size-3" />
      </span>
      <span className="tabular text-2xs whitespace-nowrap">{label}</span>
      {flow ? (
        <span className="hidden w-full items-center lg:flex">
          <span className={cn("h-0 flex-1 border-t", line)} />
          <ArrowRight className="-ml-1 size-3" />
        </span>
      ) : null}
    </div>
  );
}

/** A component that the controls have switched off: dashed, legible, not a node. */
export function OffPlaceholder({ category, name, hint }: { category: string; name: string; hint: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-dashed border-strong px-3 py-2.5">
      <span className="label-caps">{category} · not deployed</span>
      <span className="text-sm font-medium text-fg-secondary">{name}</span>
      <span className="text-xs text-muted">{hint}</span>
    </div>
  );
}

import { ArrowRight, Minus, Plus, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { formatCompact, formatCurrency } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import type {
  ArchitectureComparison,
  ComparisonChange,
  ComparisonValue,
  FieldChange,
} from "@/types/evolution";

const CHANGE_META: Record<
  ComparisonChange,
  { symbol: string; srLabel: string; Icon: LucideIcon | null; className: string }
> = {
  added: { symbol: "+", srLabel: "Added", Icon: Plus, className: "text-accent-fg" },
  removed: { symbol: "−", srLabel: "Removed", Icon: Minus, className: "text-danger-fg" },
  // No lucide "tilde"; the ~ glyph itself is the icon for a change.
  changed: { symbol: "~", srLabel: "Changed", Icon: null, className: "text-warning-fg" },
};

/** "cacheApiResponses" → "cache api responses". Display only. */
function humanizeField(field: string): string {
  return field
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_.]+/g, " ")
    .toLowerCase();
}

function formatValue(value: ComparisonValue): string {
  if (value === null) return "none";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number") return value.toLocaleString("en-US");
  return value;
}

function describeFields(subject: string, details: readonly FieldChange[]): string[] {
  if (details.length === 0) return [subject];
  return details.map(
    (d) => `${subject} ${humanizeField(d.field)} ${formatValue(d.before)} → ${formatValue(d.after)}`,
  );
}

interface ChangeLine {
  key: string;
  change: ComparisonChange;
  text: string;
}

function ChangeList({ lines, emptyText }: { lines: readonly ChangeLine[]; emptyText: string }) {
  if (lines.length === 0) return <p className="text-sm text-muted">{emptyText}</p>;
  return (
    <ul className="flex flex-col gap-1">
      {lines.map((line) => {
        const meta = CHANGE_META[line.change];
        return (
          <li key={line.key} className="flex items-start gap-2 text-sm text-fg">
            <span
              aria-hidden
              className={cn(
                "tabular mt-0.5 flex size-4 shrink-0 items-center justify-center text-sm leading-none font-semibold",
                meta.className,
              )}
            >
              {meta.Icon ? <meta.Icon className="size-3.5" /> : meta.symbol}
            </span>
            <span className="min-w-0 break-words">
              <span className="sr-only">{meta.srLabel}: </span>
              {line.text}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function BeforeAfter({
  label,
  before,
  after,
  suffix,
}: {
  label: string;
  before: string;
  after: string;
  suffix?: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-sm border border-default bg-surface-2 px-3 py-2">
      <dt className="label-caps">{label}</dt>
      <dd className="tabular flex flex-wrap items-center gap-1.5 text-base font-semibold text-fg">
        <span>{before}</span>
        <ArrowRight aria-hidden className="size-3.5 text-muted" />
        <span className="sr-only">to</span>
        <span>
          {after}
          {suffix ? <span className="ml-1 text-xs font-normal text-fg-secondary">{suffix}</span> : null}
        </span>
      </dd>
    </div>
  );
}

/**
 * Version / stage comparison (spec §43). Presentational: renders the backend diff as-is.
 * Every change line carries a symbol and screen-reader text, never colour alone (spec §62).
 */
export function ComparisonView({ comparison }: { comparison: ArchitectureComparison }) {
  const componentLines: ChangeLine[] = comparison.components.flatMap((c) =>
    (c.change === "changed" ? describeFields(c.name, c.details) : [c.name]).map((text, i) => ({
      key: `${c.nodeId}-${c.change}-${i}`,
      change: c.change,
      text,
    })),
  );
  const connectionLines: ChangeLine[] = comparison.connections.flatMap((c) => {
    const subject = `${c.sourceName} → ${c.targetName}`;
    return (c.change === "changed" ? describeFields(subject, c.details) : [subject]).map((text, i) => ({
      key: `${c.edgeId}-${c.change}-${i}`,
      change: c.change,
      text,
    }));
  });

  const id = useId();
  const { capacity, cost } = comparison;
  const title = `${comparison.from.label} → ${comparison.to.label}`;

  return (
    <section aria-label={`Comparison ${title}`} className="flex flex-col gap-5">
      <h3 className="tabular text-sm font-semibold text-fg">{title}</h3>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <section aria-labelledby={`${id}-components`} className="flex min-w-0 flex-col gap-2">
          <h4 id={`${id}-components`} className="label-caps">
            Components
          </h4>
          <ChangeList lines={componentLines} emptyText="No component changes." />
        </section>
        <section aria-labelledby={`${id}-connections`} className="flex min-w-0 flex-col gap-2">
          <h4 id={`${id}-connections`} className="label-caps">
            Connections
          </h4>
          <ChangeList lines={connectionLines} emptyText="No connection changes." />
        </section>
      </div>

      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <BeforeAfter
          label="Capacity"
          before={formatCompact(capacity.beforeMaxDailyActiveUsers)}
          after={formatCompact(capacity.afterMaxDailyActiveUsers)}
          suffix="DAU"
        />
        <BeforeAfter
          label="Cost"
          before={formatCurrency(cost.before, cost.currency)}
          after={formatCurrency(cost.after, cost.currency)}
          suffix="/month"
        />
      </dl>
    </section>
  );
}

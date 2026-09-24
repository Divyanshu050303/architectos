import { ArrowRight, Crosshair } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SeverityBadge } from "@/features/validation/severity";
import { cn } from "@/lib/utils";
import type { DriftItem } from "@/types/discovery";

import { DRIFT_STATUS_META } from "../meta";

export interface DriftTableProps {
  items: readonly DriftItem[];
  /** Column header context, e.g. "Architecture v3". */
  expectedLabel: string;
  /** Column header context, e.g. "AWS". */
  actualLabel: string;
  locateHref: (nodeId: string) => string;
  emptyMessage: React.ReactNode;
}

/** Two-column EXPECTED | ACTUAL comparison with differences highlighted (spec §45). */
export function DriftTable({ items, expectedLabel, actualLabel, locateHref, emptyMessage }: DriftTableProps) {
  return (
    <div className="w-full overflow-x-auto" tabIndex={0}>
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Expected architecture compared with actual infrastructure</caption>
        <thead>
          <tr className="bg-surface-2">
            <th scope="col" className="label-caps border-b border-default px-4 py-2 text-left">
              Status
            </th>
            <th scope="col" className="label-caps border-b border-default px-3 py-2 text-left">
              Subject
            </th>
            <th scope="col" className="border-b border-l border-default px-3 py-2 text-left">
              <span className="label-caps block">Expected</span>
              <span className="text-2xs font-normal text-muted">{expectedLabel}</span>
            </th>
            <th scope="col" className="w-6 border-b border-default px-0 py-2">
              <span className="sr-only">Comparison</span>
            </th>
            <th scope="col" className="border-r border-b border-default px-3 py-2 text-left">
              <span className="label-caps block">Actual</span>
              <span className="text-2xs font-normal text-muted">{actualLabel}</span>
            </th>
            <th scope="col" className="label-caps border-b border-default px-3 py-2 text-left">
              Severity
            </th>
            <th scope="col" className="border-b border-default px-4 py-2 text-right">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={7} className="px-4 py-8 text-center text-sm text-fg-secondary">
                {emptyMessage}
              </td>
            </tr>
          ) : null}
          {items.map((item) => {
            const meta = DRIFT_STATUS_META[item.status];
            const differs = item.status !== "matching";
            return (
              <tr key={item.id} className={cn("align-middle", differs ? "" : "text-fg-secondary")}>
                <td className="border-b border-default px-4 py-2.5">
                  <Badge tone={meta.tone} title={meta.description}>
                    <meta.Icon aria-hidden />
                    {meta.label}
                  </Badge>
                </td>
                <th scope="row" className="border-b border-default px-3 py-2.5 text-left font-medium text-fg">
                  {item.subject}
                </th>
                <td className="border-b border-l border-default px-3 py-2.5">
                  <span
                    className={cn(
                      "tabular inline-block rounded-sm px-1.5 py-0.5",
                      item.status === "unexpected" ? "text-muted italic" : "text-fg",
                    )}
                  >
                    {item.expected}
                  </span>
                </td>
                <td className="border-b border-default px-0 py-2.5 text-center">
                  {differs ? (
                    <span className="text-warning-fg" aria-hidden>
                      ≠
                    </span>
                  ) : (
                    <span className="text-muted" aria-hidden>
                      =
                    </span>
                  )}
                </td>
                <td className="border-r border-b border-default px-3 py-2.5">
                  <span className={cn("tabular inline-block rounded-sm px-1.5 py-0.5", meta.actualClass)}>
                    {item.actual}
                  </span>
                </td>
                <td className="border-b border-default px-3 py-2.5">
                  {differs ? (
                    <SeverityBadge severity={item.severity} />
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td className="border-b border-default px-4 py-2.5 text-right">
                  {item.nodeId ? (
                    <Button asChild size="sm" variant="ghost">
                      <Link
                        href={locateHref(item.nodeId)}
                        aria-label={`Locate ${item.subject} on the canvas`}
                      >
                        <Crosshair aria-hidden className="size-3.5" />
                        Locate
                        <ArrowRight aria-hidden className="size-3" />
                      </Link>
                    </Button>
                  ) : (
                    <span className="text-2xs text-muted">Not in architecture</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

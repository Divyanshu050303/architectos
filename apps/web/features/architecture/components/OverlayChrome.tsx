"use client";

/**
 * Canvas chrome for analysis overlays (spec §68–72): a small legend per mode, a
 * canvas-level summary pill with the backend's headline figure, and a "Not analyzed"
 * hint linking to the page that runs the analysis. Presentational only.
 */
import { Info } from "lucide-react";
import Link from "next/link";

import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AnalysisMode } from "@/types/architecture";

import { MODE_LABELS } from "../constants";

// --- Legend -------------------------------------------------------------------

function Swatch({ className }: { className: string }) {
  return <span aria-hidden className={cn("inline-block size-2 shrink-0 rounded-full", className)} />;
}

function Line({ className }: { className: string }) {
  return <span aria-hidden className={cn("inline-block h-0 w-5 shrink-0 border-t-2", className)} />;
}

function Item({ children }: { children: React.ReactNode }) {
  return <li className="flex items-center gap-1.5 whitespace-nowrap">{children}</li>;
}

const STATUS_ITEMS = (
  <>
    <Item>
      <Swatch className="bg-accent-strong" /> Healthy
    </Item>
    <Item>
      <Swatch className="bg-warning" /> Warning
    </Item>
    <Item>
      <Swatch className="bg-danger" /> Critical
    </Item>
  </>
);

function legendItems(mode: AnalysisMode): React.ReactNode {
  switch (mode) {
    case "topology":
      return null;
    case "capacity":
      return (
        <>
          {STATUS_ITEMS}
          <Item>
            <Badge tone="danger">Bottleneck</Badge>
          </Item>
          <Item>
            <span className="rounded-full border border-default px-1.5 text-2xs">RPS</span> Throughput
          </Item>
        </>
      );
    case "reliability":
      return (
        <>
          <Item>
            <Badge tone="warning">⚠ SPOF</Badge> Single point of failure
          </Item>
          <Item>
            <Line className="border-danger" /> Critical dependency
          </Item>
        </>
      );
    case "security":
      return (
        <>
          <Item>
            <Badge tone="warning">Public</Badge>
            <Badge tone="info">Internal</Badge>
            <Badge tone="neutral">Private</Badge>
          </Item>
          <Item>
            <span
              aria-hidden
              className="inline-block h-3 w-5 rounded-sm border border-dashed border-info/60 bg-info-soft/60"
            />
            Trust boundary
          </Item>
          <Item>
            <span className="rounded-full border border-info/40 px-1.5 text-2xs text-info-fg">→</span> Crosses
            a boundary
          </Item>
        </>
      );
    case "cost":
      return (
        <Item>
          <span className="tabular text-fg">$/mo</span> Monthly estimate per component
        </Item>
      );
    case "observability":
      return (
        <>
          <Item>
            <span className="inline-flex size-4 items-center justify-center rounded-sm border border-info/40 bg-info-soft text-2xs font-semibold text-info-fg">
              M
            </span>
            Covered
          </Item>
          <Item>
            <span className="inline-flex size-4 items-center justify-center rounded-sm border border-dashed border-control text-2xs font-semibold text-muted line-through">
              T
            </span>
            Missing
          </Item>
          <Item>
            <span className="text-muted">M L T A</span> Metrics · Logs · Traces · Alerts
          </Item>
        </>
      );
    case "simulation":
      return (
        <>
          <Item>
            <Swatch className="bg-control" /> Normal
          </Item>
          <Item>
            <Swatch className="bg-danger" /> Failed
          </Item>
          <Item>
            <Swatch className="bg-warning" /> Impacted
          </Item>
          <Item>
            <span
              aria-hidden
              className="inline-block size-2.5 rounded-sm border border-dashed border-danger"
            />
            Potential failure
          </Item>
        </>
      );
  }
}

export function OverlayLegend({ mode, className }: { mode: AnalysisMode; className?: string }) {
  const items = legendItems(mode);
  if (!items) return null;
  return (
    <section
      aria-label={`${MODE_LABELS[mode]} legend`}
      className={cn(
        "rounded-md border border-default bg-surface/95 px-2.5 py-1.5 text-2xs text-fg-secondary shadow-subtle",
        className,
      )}
    >
      <ul className="flex flex-wrap items-center gap-x-3 gap-y-1">{items}</ul>
    </section>
  );
}

// --- Summary pill ---------------------------------------------------------------

export interface OverlaySummaryProps {
  label: string;
  value: string;
  detail?: string;
  href: string;
  className?: string;
}

/** Canvas-level headline from the backend, e.g. "Total $1,240/mo" (spec §71). */
export function OverlaySummary({ label, value, detail, href, className }: OverlaySummaryProps) {
  return (
    <div
      role="status"
      className={cn(
        "flex items-center gap-2 rounded-full border border-default bg-surface px-3 py-1 text-xs shadow-subtle",
        className,
      )}
    >
      <span className="text-muted">{label}</span>
      <span className="tabular font-semibold text-fg">{value}</span>
      {detail ? <span className="tabular text-muted">{detail}</span> : null}
      <ProvenanceTag kind="calculated" />
      <Link
        href={href}
        className="font-medium text-fg-secondary underline-offset-2 hover:text-fg hover:underline"
      >
        Details
      </Link>
    </div>
  );
}

// --- Not analyzed hint ------------------------------------------------------------

export interface OverlayHintProps {
  text: string;
  href: string;
  action?: string;
  className?: string;
}

export function OverlayHint({ text, href, action = "Open", className }: OverlayHintProps) {
  return (
    <div
      role="status"
      className={cn(
        "flex items-center gap-2 rounded-md border border-default bg-surface px-3 py-1.5 text-xs text-fg-secondary shadow-subtle",
        className,
      )}
    >
      <Info aria-hidden className="size-3.5 text-info-fg" />
      {text}
      <Link href={href} className="font-medium text-fg underline-offset-2 hover:underline">
        {action}
      </Link>
    </div>
  );
}

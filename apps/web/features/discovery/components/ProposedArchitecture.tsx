import { ArrowRight, Minus, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { nodeName } from "@/lib/graph";
import { cn } from "@/lib/utils";
import type { Architecture, ArchitectureNode } from "@/types/architecture";

import { COMPONENT_TYPE_LABEL, type ComponentType } from "../meta";

/** Read-only lanes, left to right in request order: edge → compute → data → platform. */
const LANES: readonly { label: string; types: readonly ComponentType[] }[] = [
  { label: "Edge", types: ["client", "cdn", "load_balancer", "gateway"] },
  { label: "Compute", types: ["service", "worker"] },
  { label: "Data", types: ["database", "cache", "queue", "storage"] },
  { label: "Platform", types: ["observability", "external"] },
];

export interface ArchitectureChanges {
  added: ArchitectureNode[];
  removed: ArchitectureNode[];
  unchanged: number;
}

/**
 * Which components the proposal adds or drops relative to `current`, by component id.
 * A structural preview for the review step only: no analysis of the architecture.
 */
export function componentChanges(proposed: Architecture, current: Architecture): ArchitectureChanges {
  const currentIds = new Set(current.nodes.map((n) => n.id));
  const proposedIds = new Set(proposed.nodes.map((n) => n.id));
  const added = proposed.nodes.filter((n) => !currentIds.has(n.id));
  const removed = current.nodes.filter((n) => !proposedIds.has(n.id));
  return { added, removed, unchanged: proposed.nodes.length - added.length };
}

/** Components grouped by type plus their connections (spec §44 REVIEW). */
export function ProposedArchitecturePreview({
  proposed,
  addedIds,
}: {
  proposed: Architecture;
  /** Ids to mark "New" (absent from the current architecture). */
  addedIds: ReadonlySet<string>;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {LANES.map((lane) => {
          const groups = lane.types
            .map((type) => ({ type, nodes: proposed.nodes.filter((n) => n.type === type) }))
            .filter((g) => g.nodes.length > 0);
          return (
            <section
              key={lane.label}
              aria-label={`${lane.label} components`}
              className="flex min-w-0 flex-col gap-2 rounded-sm border border-default bg-sunken p-2.5"
            >
              <h4 className="label-caps">{lane.label}</h4>
              {groups.length === 0 ? <p className="text-xs text-muted">None discovered</p> : null}
              {groups.map((group) => (
                <div key={group.type} className="flex flex-col gap-1">
                  <p className="text-2xs font-medium text-fg-secondary">
                    {COMPONENT_TYPE_LABEL[group.type]}{" "}
                    <span className="tabular text-muted">{group.nodes.length}</span>
                  </p>
                  <ul className="flex flex-col gap-1">
                    {group.nodes.map((node) => {
                      const isNew = addedIds.has(node.id);
                      return (
                        <li
                          key={node.id}
                          className={cn(
                            "flex min-w-0 items-center justify-between gap-2 rounded-sm border bg-surface px-2 py-1",
                            isNew ? "border-accent/50" : "border-default",
                          )}
                        >
                          <span className="flex min-w-0 flex-col">
                            <span className="truncate text-xs font-medium text-fg">{node.name}</span>
                            <span className="tabular truncate text-2xs text-muted">{node.technology}</span>
                          </span>
                          {isNew ? (
                            <Badge tone="accent">
                              <Plus aria-hidden />
                              New
                            </Badge>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </section>
          );
        })}
      </div>

      <details className="group rounded-sm border border-default">
        <summary className="flex cursor-pointer items-center justify-between px-3 py-2 text-xs font-medium text-fg-secondary hover:text-fg">
          <span>
            Connections <span className="tabular text-muted">{proposed.edges.length}</span>
          </span>
          <span className="text-2xs text-muted group-open:hidden">Show</span>
          <span className="hidden text-2xs text-muted group-open:inline">Hide</span>
        </summary>
        <ul className="grid grid-cols-1 gap-x-6 gap-y-1 border-t border-default px-3 py-2 sm:grid-cols-2">
          {proposed.edges.map((edge) => (
            <li key={edge.id} className="flex min-w-0 items-center gap-1.5 text-xs text-fg">
              <span className="truncate">{nodeName(proposed, edge.source)}</span>
              <ArrowRight aria-label="to" className="size-3 shrink-0 text-muted" />
              <span className="truncate">{nodeName(proposed, edge.target)}</span>
              {edge.protocol ? <span className="tabular text-2xs text-muted">{edge.protocol}</span> : null}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/** "Compared with v3: +1 added · −1 removed · 10 unchanged" with names. */
export function ChangeSummary({
  changes,
  currentVersion,
}: {
  changes: ArchitectureChanges;
  currentVersion: number;
}) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p className="text-fg-secondary">
        Compared with current <span className="tabular text-fg">v{currentVersion}</span>
      </p>
      <dl className="grid grid-cols-3 gap-2">
        <Count label="Added" value={changes.added.length} tone="accent" Icon={Plus} />
        <Count label="Removed" value={changes.removed.length} tone="danger" Icon={Minus} />
        <Count label="Unchanged" value={changes.unchanged} tone="neutral" Icon={null} />
      </dl>
      {changes.added.length > 0 ? (
        <p className="text-xs text-fg-secondary">
          <span className="font-medium text-fg">Added:</span> {changes.added.map((n) => n.name).join(", ")}
        </p>
      ) : null}
      {changes.removed.length > 0 ? (
        <p className="text-xs text-fg-secondary">
          <span className="font-medium text-fg">Not found in infrastructure:</span>{" "}
          {changes.removed.map((n) => n.name).join(", ")}
        </p>
      ) : null}
    </div>
  );
}

function Count({
  label,
  value,
  tone,
  Icon,
}: {
  label: string;
  value: number;
  tone: "accent" | "danger" | "neutral";
  Icon: typeof Plus | null;
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-sm border border-default bg-surface px-2.5 py-1.5">
      <dt className="label-caps">{label}</dt>
      <dd
        className={cn(
          "tabular flex items-center gap-1 text-base font-semibold",
          value > 0 && tone === "accent" && "text-accent-fg",
          value > 0 && tone === "danger" && "text-danger-fg",
          (value === 0 || tone === "neutral") && "text-fg",
        )}
      >
        {Icon && value > 0 ? <Icon aria-hidden className="size-3.5" /> : null}
        {value}
      </dd>
    </div>
  );
}

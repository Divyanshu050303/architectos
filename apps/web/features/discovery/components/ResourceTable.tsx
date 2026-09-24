"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { DiscoveredResource, DiscoveryConnectorKind, DiscoveryRun } from "@/types/discovery";

import {
  COMPONENT_TYPE_SINGULAR,
  connectorLabel,
  RESOURCE_STATUS_META,
  RESOURCE_STATUSES,
  type ResourceStatus,
} from "../meta";

export type ResourceStatusFilter = ResourceStatus | "all";

export interface ResourceTableProps {
  resources: readonly DiscoveredResource[];
  summary: DiscoveryRun["summary"];
  /** The run is still discovering: show a scanning row instead of "no resources". */
  scanning: boolean;
  status: ResourceStatusFilter;
  onStatusChange: (status: ResourceStatusFilter) => void;
}

const ALL = "all";

/** Dense, filterable inventory of discovered resources (spec §44 DISCOVER / NORMALIZE). */
export function ResourceTable({ resources, summary, scanning, status, onStatusChange }: ResourceTableProps) {
  const [provider, setProvider] = useState<DiscoveryConnectorKind | typeof ALL>(ALL);
  const [type, setType] = useState<string>(ALL);
  const [query, setQuery] = useState("");

  const providers = useMemo(() => [...new Set(resources.map((r) => r.provider))], [resources]);
  const types = useMemo(() => [...new Set(resources.map((r) => r.resourceType))].sort(), [resources]);

  const q = query.trim().toLowerCase();
  const visible = resources.filter(
    (r) =>
      (provider === ALL || r.provider === provider) &&
      (type === ALL || r.resourceType === type) &&
      (status === ALL || r.status === status) &&
      (!q || r.name.toLowerCase().includes(q) || r.id.toLowerCase().includes(q)),
  );

  const statusCounts: Record<ResourceStatusFilter, number> = {
    all: summary.total,
    mapped: summary.mapped,
    unmapped: summary.unmapped,
    ignored: summary.ignored,
  };

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-end gap-3 border-b border-default px-4 py-3">
        <div role="group" aria-label="Filter by mapping status" className="flex flex-wrap gap-1">
          {([ALL, ...RESOURCE_STATUSES] as const).map((s) => {
            const pressed = status === s;
            const meta = s === ALL ? null : RESOURCE_STATUS_META[s];
            return (
              <button
                key={s}
                type="button"
                aria-pressed={pressed}
                onClick={() => onStatusChange(s)}
                className={cn(
                  "inline-flex h-7 items-center gap-1.5 rounded-sm border px-2 text-xs font-medium transition-colors",
                  pressed
                    ? "border-accent-strong bg-accent-soft text-accent-fg"
                    : "border-default bg-surface text-fg-secondary hover:bg-surface-2 hover:text-fg",
                )}
              >
                {meta ? <meta.Icon aria-hidden className="size-3.5" /> : null}
                {meta ? meta.label : "All"}
                <span className="tabular text-2xs text-muted">{statusCounts[s]}</span>
              </button>
            );
          })}
        </div>
        <div className="ml-auto flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-2xs font-medium text-muted">
            Provider
            <Select
              className="w-32"
              value={provider}
              onChange={(e) => setProvider(providers.find((p) => p === e.target.value) ?? ALL)}
              options={[
                { value: ALL, label: "All providers" },
                ...providers.map((p) => ({ value: p, label: connectorLabel(p) })),
              ]}
            />
          </label>
          <label className="flex flex-col gap-1 text-2xs font-medium text-muted">
            Type
            <Select
              className="tabular w-56"
              value={type}
              onChange={(e) => setType(e.target.value)}
              options={[{ value: ALL, label: "All types" }, ...types.map((t) => ({ value: t, label: t }))]}
            />
          </label>
          <label className="relative flex flex-col gap-1 text-2xs font-medium text-muted">
            Search
            <Search aria-hidden className="pointer-events-none absolute bottom-2 left-2 size-4 text-muted" />
            <Input
              type="search"
              className="w-48 pl-7"
              placeholder="Name or ID"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
      </div>

      <p className="tabular px-4 py-2 text-2xs text-muted" aria-live="polite">
        Showing {visible.length} of {resources.length} resources
      </p>

      <div className="max-h-[440px] overflow-auto border-t border-default">
        <table className="w-full border-collapse text-xs">
          <caption className="sr-only">Discovered resources</caption>
          <thead className="sticky top-0 z-10 bg-surface-2">
            <tr>
              {["Status", "Resource ID", "Name", "Type", "Provider", "Region", "Maps to"].map((h) => (
                <th key={h} scope="col" className="label-caps border-b border-default px-3 py-1.5 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const meta = RESOURCE_STATUS_META[r.status];
              return (
                <tr key={`${r.provider}:${r.id}`} className="hover:bg-surface-2/60">
                  <td className="border-b border-default px-3 py-1">
                    <Badge tone={meta.tone}>
                      <meta.Icon aria-hidden />
                      {meta.label}
                    </Badge>
                  </td>
                  <td className="tabular border-b border-default px-3 py-1 text-fg-secondary">{r.id}</td>
                  <td className="border-b border-default px-3 py-1 font-medium text-fg">{r.name}</td>
                  <td className="tabular border-b border-default px-3 py-1 text-fg-secondary">
                    {r.resourceType}
                  </td>
                  <td className="border-b border-default px-3 py-1 text-fg-secondary">
                    {connectorLabel(r.provider)}
                  </td>
                  <td className="tabular border-b border-default px-3 py-1 text-fg-secondary">
                    {r.region ?? "global"}
                  </td>
                  <td className="border-b border-default px-3 py-1 text-fg">
                    {r.mappedNodeType ? (
                      COMPONENT_TYPE_SINGULAR[r.mappedNodeType]
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {scanning && resources.length === 0
              ? [0, 1, 2, 3, 4].map((i) => (
                  <tr key={`scan-${i}`}>
                    <td colSpan={7} className="border-b border-default px-3 py-1.5">
                      <Skeleton className="h-4" />
                    </td>
                  </tr>
                ))
              : null}
            {!scanning && visible.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-sm text-fg-secondary">
                  {resources.length === 0
                    ? "The scan found no resources. Check the connector's scope (region, context or path) and scan again."
                    : "No resources match these filters. Clear a filter to see more."}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

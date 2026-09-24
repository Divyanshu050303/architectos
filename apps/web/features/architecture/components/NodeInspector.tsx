"use client";

/**
 * Component inspector (spec §28–29): header plus one tab per kind of backend data.
 * Tabs live in ./inspector; this file only composes them. Every edit is emitted as a
 * semantic ArchitectureCommand; numbers come from the backend engines (spec §111).
 */
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import type { NodeVisualState } from "@/types/component";

import { COMPONENT_TYPE_META } from "../constants";
import { ComponentIcon } from "./ComponentIcon";
import { CapacityTab, ConstraintsTab } from "./inspector/CapacityTab";
import { ConfigurationTab } from "./inspector/ConfigurationTab";
import { CostTab } from "./inspector/CostTab";
import { EvidenceTab } from "./inspector/EvidenceTab";
import { FailureModesTab } from "./inspector/FailureModesTab";
import { NameEditor } from "./inspector/NameEditor";
import { ObservabilityTab } from "./inspector/ObservabilityTab";
import { OverviewTab } from "./inspector/OverviewTab";
import { SecurityTab } from "./inspector/SecurityTab";
import { availableTabs, TAB_LABELS } from "./inspector/tabs";
import type { NodeInspectorProps } from "./inspector/types";

export { humanizeKey } from "./inspector/shared";
export type { InspectorTab } from "./inspector/tabs";
export type {
  NodeCostDetails,
  NodeInspectorProps,
  NodeObservabilityDetails,
  NodeSecurityDetails,
} from "./inspector/types";

const STATUS_DOT: Partial<Record<NodeVisualState, string>> = {
  healthy: "bg-accent-strong",
  warning: "bg-warning",
  critical: "bg-danger",
  loading: "bg-info motion-safe:animate-pulse",
  simulating: "bg-info",
};

export function NodeInspector(props: NodeInspectorProps) {
  const { node, status, statusLabel, tab, onTabChange } = props;
  const tabs = availableTabs(props);
  const activeTab = (tabs as string[]).includes(tab) ? tab : "overview";
  const { category } = COMPONENT_TYPE_META[node.type];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-col gap-1 px-4 pt-3 pb-3">
        <div className="flex items-center gap-1.5">
          <ComponentIcon node={node} className="size-3.5 text-muted" />
          <span className="label-caps">{category}</span>
        </div>
        <NameEditor node={node} editable={props.editable} onCommand={props.onCommand} />
        <p className="text-xs text-fg-secondary">{node.technology}</p>
        {node.description ? <p className="text-xs text-muted">{node.description}</p> : null}
        <p
          className="mt-1 inline-flex items-center gap-1.5 text-xs font-medium text-fg"
          aria-busy={status === "loading" || undefined}
        >
          <span aria-hidden className={cn("size-1.5 rounded-full", STATUS_DOT[status] ?? "bg-control")} />
          <span className="sr-only">Status: </span>
          {statusLabel}
        </p>
      </header>

      <Tabs value={activeTab} onValueChange={onTabChange} className="flex min-h-0 flex-1 flex-col">
        <TabsList aria-label="Component details" className="flex-wrap gap-x-1 gap-y-0 overflow-visible">
          {tabs.map((t) => (
            <TabsTrigger key={t} value={t}>
              {TAB_LABELS[t]}
            </TabsTrigger>
          ))}
        </TabsList>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <TabsContent value="overview">
            <OverviewTab {...props} />
          </TabsContent>
          {tabs.includes("configuration") ? (
            <TabsContent value="configuration">
              <ConfigurationTab {...props} />
            </TabsContent>
          ) : null}
          {tabs.includes("capacity") ? (
            <TabsContent value="capacity">
              <CapacityTab {...props} />
            </TabsContent>
          ) : null}
          {tabs.includes("constraints") ? (
            <TabsContent value="constraints">
              <ConstraintsTab {...props} />
            </TabsContent>
          ) : null}
          {tabs.includes("failure-modes") ? (
            <TabsContent value="failure-modes">
              <FailureModesTab {...props} />
            </TabsContent>
          ) : null}
          {tabs.includes("security") && props.security ? (
            <TabsContent value="security">
              <SecurityTab security={props.security} onOpenEvidence={props.onOpenEvidence} />
            </TabsContent>
          ) : null}
          {tabs.includes("observability") && props.observability ? (
            <TabsContent value="observability">
              <ObservabilityTab observability={props.observability} />
            </TabsContent>
          ) : null}
          {tabs.includes("cost") && props.cost ? (
            <TabsContent value="cost">
              <CostTab cost={props.cost} onOpenEvidence={props.onOpenEvidence} />
            </TabsContent>
          ) : null}
          {tabs.includes("evidence") ? (
            <TabsContent value="evidence">
              <EvidenceTab {...props} />
            </TabsContent>
          ) : null}
        </div>
      </Tabs>
    </div>
  );
}

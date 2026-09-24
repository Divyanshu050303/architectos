/**
 * Props shared by the component inspector and its tabs (spec §28–29). Every edit is
 * emitted as a semantic ArchitectureCommand; analysis figures come from the backend.
 */
import type { Architecture, ArchitectureNode } from "@/types/architecture";
import type { ComponentUtilization } from "@/types/capacity";
import type { NodeVisualState } from "@/types/component";
import type { NodeCost } from "@/types/cost";
import type { ObservabilityCoverage, ObservabilityGap, Slo } from "@/types/observability";
import type { Exposure, SecurityControls, Threat } from "@/types/security";
import type { Finding } from "@/types/validation";

import type { ArchitectureCommand } from "../../types";

/** Backend security posture for one component (spec §29). */
export interface NodeSecurityDetails {
  exposure: Exposure | null;
  controls: SecurityControls | null;
  threats: readonly Threat[];
}

/** Backend observability coverage for one component (spec §29). */
export interface NodeObservabilityDetails {
  coverage: ObservabilityCoverage | null;
  slos: readonly Slo[];
  gap: ObservabilityGap | null;
}

/** Backend cost estimate for one component (spec §29, §71). */
export interface NodeCostDetails {
  cost: NodeCost;
  currency: string;
  /** Evidence behind the estimate, for "Why?". */
  evidenceId: string | null;
}

export interface NodeInspectorProps {
  projectId: string;
  node: ArchitectureNode;
  architecture: Pick<Architecture, "nodes" | "edges">;
  /** Backend capacity rows for this node. */
  utilization: readonly ComponentUtilization[];
  /** Open validation findings that reference this node. */
  findings: readonly Finding[];
  status: NodeVisualState;
  statusLabel: string;
  tab: string;
  onTabChange: (tab: string) => void;
  editable: boolean;
  onCommand: (command: ArchitectureCommand) => void;
  onOpenEvidence: (evidenceId: string) => void;
  onSelectNode: (nodeId: string) => void;
  security?: NodeSecurityDetails | null;
  observability?: NodeObservabilityDetails | null;
  cost?: NodeCostDetails | null;
}

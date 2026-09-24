/**
 * Domain → React Flow node adapter (spec §10). React Flow types stay inside
 * features/architecture. Node status, metrics and badges come only from backend
 * analyses (capacity, validation, reliability, security, cost, observability,
 * simulation); nothing is calculated here, only selected and formatted (spec §111).
 */
import type { Node } from "@xyflow/react";

import { formatCurrency, formatPercent as formatRatio } from "@/lib/formatting";
import type { AnalysisMode, ArchitectureNode, Position } from "@/types/architecture";
import type { CapacityAnalysis, ComponentUtilization } from "@/types/capacity";
import type { NodeVisualState } from "@/types/component";
import type { CostEstimate } from "@/types/cost";
import type { ObservabilityAnalysis, ObservabilityCoverage } from "@/types/observability";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { ExposureLevel, SecurityAnalysis, Threat } from "@/types/security";
import type { Finding, Severity } from "@/types/validation";

import { COMPONENT_TYPE_META } from "../constants";
import type { SimulationNodeState } from "./simulation-playback";

export type NodeBadgeTone = "warning" | "danger" | "info" | "accent" | "neutral";

export interface NodeBadge {
  label: string;
  tone: NodeBadgeTone;
}

/** One observability signal on a node (spec §68): present or missing, never colour-only. */
export interface CoverageChip {
  key: "M" | "L" | "T" | "A";
  label: string;
  present: boolean;
}

export type ArchitectureNodeData = {
  node: ArchitectureNode;
  category: string;
  status: NodeVisualState;
  /** Text label for the status: status is never colour-only (spec §62). */
  statusLabel: string;
  metric: { label: string; value: string } | null;
  /** Backend-computed used/limit of the metric row, for the utilisation bar. */
  utilization: number | null;
  badges: NodeBadge[];
  highlighted: boolean;
  dimmed: boolean;
  mode: AnalysisMode;
  /** Observability overlay only. */
  coverage?: readonly CoverageChip[] | null;
  /** Simulation overlay only: this node's state at the current playback step. */
  simulation?: SimulationNodeState | null;
  /** The node changed in the version that just loaded (spec §100 version transition). */
  versionChanged?: boolean;
  [key: string]: unknown;
};

export type ArchitectureFlowNode = Node<ArchitectureNodeData, "architecture">;

export interface OverlayContext {
  mode: AnalysisMode;
  capacity: CapacityAnalysis | null;
  findings: readonly Finding[];
  highlightedNodeIds: ReadonlySet<string>;
  selectedNodeIds: ReadonlySet<string>;
  reliability?: ReliabilityAnalysis | null;
  security?: SecurityAnalysis | null;
  cost?: CostEstimate | null;
  observability?: ObservabilityAnalysis | null;
  /** Node states of the current simulation playback step; null when there is no finished run. */
  simulation?: ReadonlyMap<string, SimulationNodeState> | null;
  /**
   * Nodes taking part in the simulation run, set only while playback is running
   * (spec §25 `simulating`).
   */
  simulationActive?: ReadonlySet<string> | null;
  /** The active overlay's analysis for the current version is still being fetched (spec §25 `loading`). */
  loading?: boolean;
  /** Nodes that changed in the version that just loaded. */
  versionChangedNodeIds?: ReadonlySet<string> | null;
}

export type AnalyzedStatus = "default" | "healthy" | "warning" | "critical";

const STATUS_RANK: Record<AnalyzedStatus, number> = { default: 0, healthy: 1, warning: 2, critical: 3 };

export const STATUS_LABELS: Record<AnalyzedStatus, string> = {
  default: "Not analyzed",
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
};

export function worstStatus(a: AnalyzedStatus, b: AnalyzedStatus): AnalyzedStatus {
  return STATUS_RANK[b] > STATUS_RANK[a] ? b : a;
}

const SEVERITY_STATUS: Record<Severity, AnalyzedStatus> = {
  critical: "critical",
  high: "warning",
  medium: "warning",
  low: "healthy",
  info: "healthy",
};

const SEVERITY_RANK: Record<Severity, number> = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };

function findingStatus(finding: Finding): AnalyzedStatus {
  if (finding.severity === "critical") return "critical";
  if (finding.severity === "high") return "warning";
  return "healthy";
}

function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

function isSpof(finding: Finding): boolean {
  return finding.ruleId.includes("single_point_of_failure");
}

export const EXPOSURE_BADGE: Record<ExposureLevel, NodeBadge> = {
  public: { label: "Public", tone: "warning" },
  internal: { label: "Internal", tone: "info" },
  private: { label: "Private", tone: "neutral" },
};

const THREAT_LABEL: Record<Severity, string> = {
  critical: "Critical threat",
  high: "High threat",
  medium: "Medium threat",
  low: "Low threat",
  info: "Informational",
};

export const COVERAGE_SIGNALS: readonly {
  key: CoverageChip["key"];
  signal: keyof ObservabilityCoverage;
  label: string;
}[] = [
  { key: "M", signal: "metrics", label: "Metrics" },
  { key: "L", signal: "logs", label: "Logs" },
  { key: "T", signal: "traces", label: "Traces" },
  { key: "A", signal: "alerts", label: "Alerts" },
];

const SIMULATION_STATUS: Record<SimulationNodeState, { status: AnalyzedStatus; label: string }> = {
  normal: { status: "default", label: "Normal" },
  failed: { status: "critical", label: "Failed" },
  impacted: { status: "warning", label: "Impacted" },
  cascade: { status: "critical", label: "Potential failure" },
};

export const LOADING_LABEL = "Analyzing…";
export const SIMULATING_LABEL = "Simulating";

/**
 * A component switched off in its configuration (spec §25 `disabled`): explicitly
 * disabled, or scaled to zero replicas. Read from the architecture, not calculated.
 */
export function disabledReason(node: Pick<ArchitectureNode, "configuration">): string | null {
  const { enabled, replicas } = node.configuration;
  if (enabled === false) return "Disabled";
  if (replicas === 0) return "Disabled · 0 replicas";
  return null;
}

function pushIndexed<T>(map: Map<string, T[]>, key: string, value: T) {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

/** Index backend results by node once per render instead of once per node. */
function indexByNode(ctx: OverlayContext) {
  const peakRow = new Map<string, ComponentUtilization>();
  for (const row of ctx.capacity?.utilization ?? []) {
    const current = peakRow.get(row.nodeId);
    if (!current || row.utilization > current.utilization) peakRow.set(row.nodeId, row);
  }
  const findingsByNode = new Map<string, Finding[]>();
  for (const finding of ctx.findings) {
    if (finding.status !== "open") continue;
    for (const nodeId of new Set(finding.nodeIds)) pushIndexed(findingsByNode, nodeId, finding);
  }
  return { peakRow, findingsByNode };
}

function indexReliability(analysis: ReliabilityAnalysis) {
  const spof = new Map(analysis.singlePointsOfFailure.map((s) => [s.nodeId, s]));
  const entry = new Map(analysis.entrypoints.map((e) => [e.nodeId, e.availability]));
  /** Lowest backend availability of any critical path through the node. */
  const pathAvailability = new Map<string, number>();
  for (const path of analysis.criticalPaths) {
    for (const nodeId of path.nodeIds) {
      const current = pathAvailability.get(nodeId);
      if (current === undefined || path.availability < current)
        pathAvailability.set(nodeId, path.availability);
    }
  }
  return { spof, entry, pathAvailability };
}

function indexSecurity(analysis: SecurityAnalysis) {
  const exposure = new Map(analysis.exposure.map((e) => [e.nodeId, e.level]));
  const threats = new Map<string, Threat[]>();
  for (const threat of analysis.threats) {
    for (const nodeId of new Set(threat.nodeIds)) pushIndexed(threats, nodeId, threat);
  }
  return { exposure, threats };
}

function indexObservability(analysis: ObservabilityAnalysis) {
  return {
    coverage: new Map(analysis.coverage.map((c) => [c.nodeId, c])),
    gaps: new Map(analysis.gaps.map((g) => [g.nodeId, g])),
  };
}

interface NodeOverlay {
  status: AnalyzedStatus;
  statusLabel: string;
  metric: ArchitectureNodeData["metric"];
  badges: NodeBadge[];
  coverage: readonly CoverageChip[] | null;
  simulation: SimulationNodeState | null;
}

/**
 * Node states that are not analysis results (spec §25), in priority order:
 * a failed/impacted simulation state stays visible; otherwise a switched-off
 * component reads `disabled`, a node in a playing simulation `simulating`, and a
 * node whose overlay analysis is still loading `loading`.
 */
function transientState(
  overlay: NodeOverlay,
  node: ArchitectureNode,
  { loading, simulating }: { loading: boolean; simulating: boolean },
): { status: NodeVisualState; label: string } | null {
  if (overlay.simulation && overlay.simulation !== "normal") return null;
  const disabled = disabledReason(node);
  if (disabled) return { status: "disabled", label: disabled };
  if (simulating) return { status: "simulating", label: SIMULATING_LABEL };
  if (loading) return { status: "loading", label: LOADING_LABEL };
  return null;
}

export function toFlowNodes(nodes: readonly ArchitectureNode[], ctx: OverlayContext): ArchitectureFlowNode[] {
  const { peakRow, findingsByNode } = indexByNode(ctx);
  const highlightActive = ctx.highlightedNodeIds.size > 0;
  const bottleneckNodeId = ctx.capacity?.bottleneck?.nodeId ?? null;
  const reliability =
    ctx.mode === "reliability" && ctx.reliability ? indexReliability(ctx.reliability) : null;
  const security = ctx.mode === "security" && ctx.security ? indexSecurity(ctx.security) : null;
  const observability =
    ctx.mode === "observability" && ctx.observability ? indexObservability(ctx.observability) : null;
  const costByNode =
    ctx.mode === "cost" && ctx.cost ? new Map(ctx.cost.nodes.map((n) => [n.nodeId, n.monthly])) : null;
  const simulation = ctx.mode === "simulation" ? (ctx.simulation ?? null) : null;
  const simulationActive = simulation ? (ctx.simulationActive ?? null) : null;

  return nodes.map((node) => {
    const row = peakRow.get(node.id);
    const findings = findingsByNode.get(node.id) ?? [];

    // Base lens (topology, capacity, reliability, cost): capacity status escalated by open findings.
    let status: AnalyzedStatus = row ? row.status : "default";
    for (const finding of findings) status = worstStatus(status, findingStatus(finding));

    const overlay: NodeOverlay = {
      status,
      statusLabel: STATUS_LABELS[status],
      metric: row ? { label: row.resource, value: formatPercent(row.utilization) } : null,
      badges: [],
      coverage: null,
      simulation: null,
    };

    switch (ctx.mode) {
      case "capacity":
        if (bottleneckNodeId === node.id) overlay.badges.push({ label: "Bottleneck", tone: "danger" });
        break;

      case "reliability":
        if (reliability) {
          const spof = reliability.spof.get(node.id);
          if (spof) {
            overlay.badges.push({
              label: "⚠ SPOF",
              tone: spof.severity === "critical" ? "danger" : "warning",
            });
            overlay.status = worstStatus(overlay.status, SEVERITY_STATUS[spof.severity]);
            overlay.statusLabel = STATUS_LABELS[overlay.status];
          }
          const entry = reliability.entry.get(node.id);
          const path = reliability.pathAvailability.get(node.id);
          overlay.metric =
            entry !== undefined
              ? { label: "avail.", value: formatRatio(entry, 2) }
              : path !== undefined
                ? { label: "path avail.", value: formatRatio(path, 2) }
                : null;
        } else {
          // No reliability analysis yet: fall back to validation findings.
          const spof = findings.find(isSpof);
          if (spof)
            overlay.badges.push({
              label: "⚠ SPOF",
              tone: spof.severity === "critical" ? "danger" : "warning",
            });
        }
        break;

      case "security":
        if (security) {
          const level = security.exposure.get(node.id);
          if (level) overlay.badges.push(EXPOSURE_BADGE[level]);
          const threats = security.threats.get(node.id) ?? [];
          const worst = threats.reduce<Threat | null>(
            (acc, t) => (!acc || SEVERITY_RANK[t.severity] > SEVERITY_RANK[acc.severity] ? t : acc),
            null,
          );
          overlay.status = worst ? SEVERITY_STATUS[worst.severity] : "healthy";
          overlay.statusLabel = worst ? THREAT_LABEL[worst.severity] : "No threats";
          overlay.metric =
            threats.length > 0
              ? { label: threats.length === 1 ? "threat" : "threats", value: String(threats.length) }
              : null;
        } else {
          overlay.status = "default";
          overlay.statusLabel = STATUS_LABELS.default;
          overlay.metric = null;
        }
        break;

      case "cost": {
        const monthly = costByNode?.get(node.id);
        overlay.metric =
          monthly !== undefined && ctx.cost
            ? { label: "", value: `${formatCurrency(monthly, ctx.cost.currency)}/mo` }
            : null;
        break;
      }

      case "observability": {
        const coverage = observability?.coverage.get(node.id);
        if (observability && coverage) {
          overlay.coverage = COVERAGE_SIGNALS.map(({ key, signal, label }) => ({
            key,
            label,
            present: coverage[signal] === true,
          }));
          const missing = observability.gaps.get(node.id)?.missing.length ?? 0;
          overlay.status = missing > 0 ? "warning" : "healthy";
          overlay.statusLabel = missing > 0 ? `${missing} ${missing === 1 ? "gap" : "gaps"}` : "Covered";
        } else {
          overlay.status = "default";
          overlay.statusLabel = STATUS_LABELS.default;
        }
        overlay.metric = null;
        break;
      }

      case "simulation":
        if (simulation) {
          const state = simulation.get(node.id) ?? "normal";
          overlay.simulation = state;
          overlay.status = SIMULATION_STATUS[state].status;
          overlay.statusLabel = SIMULATION_STATUS[state].label;
          overlay.metric = null;
        }
        break;

      case "topology":
        break;
    }

    const transient = transientState(overlay, node, {
      loading: ctx.loading === true,
      simulating: simulationActive?.has(node.id) === true,
    });

    const highlighted = ctx.highlightedNodeIds.has(node.id);
    return {
      id: node.id,
      type: "architecture",
      position: node.position,
      selected: ctx.selectedNodeIds.has(node.id),
      data: {
        node,
        category: COMPONENT_TYPE_META[node.type].category,
        status: transient?.status ?? overlay.status,
        statusLabel: transient?.label ?? overlay.statusLabel,
        metric: overlay.metric,
        utilization: row ? row.utilization : null,
        badges: overlay.badges,
        highlighted,
        dimmed: highlightActive && !highlighted,
        mode: ctx.mode,
        coverage: overlay.coverage,
        simulation: overlay.simulation,
        versionChanged: ctx.versionChangedNodeIds?.has(node.id) === true,
      },
    };
  });
}

/** React Flow drag results → domain positions for a MOVE_COMPONENTS command. */
export function fromFlowPositions(
  nodes: readonly { id: string; position: { x: number; y: number } }[],
): Record<string, Position> {
  return Object.fromEntries(
    nodes.map((n) => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }]),
  );
}

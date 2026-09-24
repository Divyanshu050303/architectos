/**
 * Presentation metadata for brownfield discovery (spec §44). Nothing here analyses
 * infrastructure: resource mapping, counts and the proposed architecture all come from
 * the backend discovery run. This file only decides how those results are displayed.
 */
import {
  CheckCircle2,
  Cloud,
  Container,
  FileCode2,
  type LucideIcon,
  MinusCircle,
  TriangleAlert,
} from "lucide-react";

import type { BadgeTone } from "@/components/ui/badge";
import type { LoadingStepStatus } from "@/components/feedback/LoadingSteps";
import type { ArchitectureNode } from "@/types/architecture";
import type {
  DiscoveredResource,
  DiscoveryConnectorKind,
  DiscoveryOptions,
  DiscoveryRun,
} from "@/types/discovery";

export type ResourceStatus = DiscoveredResource["status"];
export type ComponentType = ArchitectureNode["type"];

export const RESOURCE_STATUSES: readonly ResourceStatus[] = ["mapped", "unmapped", "ignored"];

export const RESOURCE_STATUS_META: Record<
  ResourceStatus,
  { label: string; tone: BadgeTone; Icon: LucideIcon }
> = {
  mapped: { label: "Mapped", tone: "accent", Icon: CheckCircle2 },
  unmapped: { label: "Unmapped", tone: "warning", Icon: TriangleAlert },
  ignored: { label: "Ignored", tone: "neutral", Icon: MinusCircle },
};

export interface ConnectorOptionField {
  key: keyof DiscoveryOptions;
  label: string;
  placeholder: string;
  description: string;
}

export const CONNECTOR_META: Record<
  DiscoveryConnectorKind,
  { label: string; Icon: LucideIcon; option: ConnectorOptionField }
> = {
  aws: {
    label: "AWS",
    Icon: Cloud,
    option: {
      key: "region",
      label: "Region",
      placeholder: "us-east-1",
      description: "Leave empty to scan the account's default region.",
    },
  },
  kubernetes: {
    label: "Kubernetes",
    Icon: Container,
    option: {
      key: "context",
      label: "Kube context",
      placeholder: "food-prod-eks",
      description: "Leave empty to use the connector's configured context.",
    },
  },
  terraform: {
    label: "Terraform",
    Icon: FileCode2,
    option: {
      key: "path",
      label: "State or plan path",
      placeholder: "infra/terraform",
      description: "Path to a Terraform state or plan file in the connected source.",
    },
  },
};

export function connectorLabel(kind: DiscoveryConnectorKind): string {
  return CONNECTOR_META[kind].label;
}

/** Plural group headings for the proposed-architecture preview. */
export const COMPONENT_TYPE_LABEL: Record<ComponentType, string> = {
  client: "Clients",
  cdn: "CDN",
  load_balancer: "Load balancers",
  gateway: "Gateways",
  service: "Services",
  worker: "Workers",
  database: "Databases",
  cache: "Caches",
  queue: "Queues",
  storage: "Storage",
  observability: "Observability",
  external: "External",
};

/** Singular component type for a single resource ("Maps to"). */
export const COMPONENT_TYPE_SINGULAR: Record<ComponentType, string> = {
  client: "Client",
  cdn: "CDN",
  load_balancer: "Load balancer",
  gateway: "Gateway",
  service: "Service",
  worker: "Worker",
  database: "Database",
  cache: "Cache",
  queue: "Queue",
  storage: "Storage",
  observability: "Observability",
  external: "External",
};

// --- Stepper ------------------------------------------------------------------

export type DiscoveryStageId = "connect" | "discover" | "normalize" | "generate" | "review" | "save";

/** "awaiting" = the stage waits for the user (pick a source, approve the proposal). */
export type DiscoveryStageStatus = LoadingStepStatus | "awaiting";

export interface DiscoveryStage {
  id: DiscoveryStageId;
  label: string;
  status: DiscoveryStageStatus;
  /** Live detail under the label, e.g. "183 resources". */
  detail: string | null;
}

const STAGES: readonly { id: DiscoveryStageId; label: string; runStep: string | null }[] = [
  { id: "connect", label: "Connect", runStep: "connect" },
  { id: "discover", label: "Discover", runStep: "discover" },
  { id: "normalize", label: "Normalize", runStep: "normalize" },
  { id: "generate", label: "Generate architecture", runStep: "generate architecture" },
  { id: "review", label: "Review", runStep: "review" },
  { id: "save", label: "Save", runStep: null },
];

function runStepStatus(run: DiscoveryRun, index: number, label: string): LoadingStepStatus {
  const step = run.steps.find((s) => s.label.trim().toLowerCase() === label) ?? run.steps[index];
  if (step) return step.status;
  return run.status === "succeeded" ? "done" : "pending";
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
}

export interface StageInput {
  run: DiscoveryRun | null;
  /** Connector chosen for a run that is starting, before the run exists. */
  startingConnector: DiscoveryConnectorKind | null;
  saving: boolean;
  savedVersion: number | null;
}

/**
 * Maps the backend run's steps onto the six stages of spec §44. The run reports
 * CONNECT → REVIEW; SAVE is the user's explicit approval (spec §89).
 */
export function discoveryStages({
  run,
  startingConnector,
  saving,
  savedVersion,
}: StageInput): DiscoveryStage[] {
  return STAGES.map(({ id, label, runStep }, index): DiscoveryStage => {
    if (!run) {
      const status: DiscoveryStageStatus =
        id === "connect" ? (startingConnector ? "running" : "awaiting") : "pending";
      return {
        id,
        label,
        status,
        detail: id === "connect" && startingConnector ? connectorLabel(startingConnector) : null,
      };
    }

    let status: DiscoveryStageStatus = runStep ? runStepStatus(run, index, runStep) : "pending";
    if (id === "review" && run.status === "succeeded") status = savedVersion !== null ? "done" : "awaiting";
    if (id === "save") status = savedVersion !== null ? "done" : saving ? "running" : "pending";
    if (run.status === "failed" && status === "running") status = "failed";

    const reached = status !== "pending";
    let detail: string | null = null;
    switch (id) {
      case "connect":
        detail = connectorLabel(run.connector);
        break;
      case "discover":
        detail = reached && run.summary.total > 0 ? plural(run.summary.total, "resource") : null;
        break;
      case "normalize":
        detail = status === "done" ? `${run.summary.mapped} mapped · ${run.summary.unmapped} unmapped` : null;
        break;
      case "generate":
        detail = run.proposedArchitecture ? plural(run.proposedArchitecture.nodes.length, "component") : null;
        break;
      case "review":
        detail = status === "awaiting" ? "Awaiting approval" : status === "done" ? "Approved" : null;
        break;
      case "save":
        detail = savedVersion !== null ? `Saved as v${savedVersion}` : null;
        break;
    }
    return { id, label, status, detail };
  });
}

/** Removes empty option values so the backend applies its defaults. */
export function cleanOptions(options: DiscoveryOptions): DiscoveryOptions {
  const clean: DiscoveryOptions = {};
  for (const key of ["region", "context", "path"] as const) {
    const value = options[key]?.trim();
    if (value) clean[key] = value;
  }
  return clean;
}

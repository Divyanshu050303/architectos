/**
 * MOCK FIXTURE BUILDERS — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Small constructors shared by the fixture modules. Fixture data, not real analysis.
 */
import type { ArchitectureEdge, ArchitectureNode } from "@/types/architecture";
import type { ComponentUtilization } from "@/types/capacity";
import type { ComponentType } from "@/types/component";

export const SEED_TIME = "2026-09-01T09:00:00.000Z";

export function mockNode(
  id: string,
  type: ComponentType,
  name: string,
  technology: string,
  position: { x: number; y: number },
  configuration: Record<string, unknown> = {},
  extra: Pick<ArchitectureNode, "description" | "domain"> = {},
): ArchitectureNode {
  return { id, type, name, technology, configuration, position, ...extra };
}

export function mockEdge(
  id: string,
  source: string,
  target: string,
  protocol: string,
  options: Partial<Pick<ArchitectureEdge, "label" | "synchronous" | "critical">> = {},
): ArchitectureEdge {
  return { id, source, target, protocol, synchronous: true, critical: true, ...options };
}

export function utilization(
  nodeId: string,
  resource: string,
  used: number,
  limit: number,
  unit: string,
  threshold: number,
  evidenceId: string | null = null,
): ComponentUtilization {
  const ratio = used / limit;
  const status = ratio >= 0.95 ? "critical" : ratio >= threshold ? "warning" : "healthy";
  return { nodeId, resource, used, limit, unit, utilization: ratio, threshold, status, evidenceId };
}

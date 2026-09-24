/**
 * Architecture IR, as the frontend receives it (spec §8).
 *
 * INTEGRATION POINT: the backend IR lives in core/architecture_ir, which has no
 * implementation yet. This schema follows spec §8 and is the frontend's proposed
 * wire contract; reconcile it with the FastAPI OpenAPI schema once published.
 */
import { z } from "zod";

export const COMPONENT_TYPES = [
  "client",
  "cdn",
  "load_balancer",
  "gateway",
  "service",
  "worker",
  "database",
  "cache",
  "queue",
  "storage",
  "observability",
  "external",
] as const;

export const ComponentTypeSchema = z.enum(COMPONENT_TYPES);

export const PositionSchema = z.object({ x: z.number(), y: z.number() });

export const ArchitectureNodeSchema = z.object({
  id: z.string().min(1),
  type: ComponentTypeSchema,
  name: z.string().min(1),
  technology: z.string(),
  /** Short role, e.g. "Primary store". */
  description: z.string().optional(),
  /** Logical domain for grouped / overview layouts (spec §65). */
  domain: z.string().optional(),
  configuration: z.record(z.string(), z.unknown()),
  position: PositionSchema,
});

export const ArchitectureEdgeSchema = z.object({
  id: z.string().min(1),
  source: z.string().min(1),
  target: z.string().min(1),
  protocol: z.string().optional(),
  label: z.string().optional(),
  synchronous: z.boolean().default(true),
  critical: z.boolean().default(true),
});

export const AssumptionSchema = z.object({
  id: z.string(),
  statement: z.string(),
  source: z.enum(["user", "ai", "default"]).default("default"),
});

/** Who produced a version: a user edit, an approved AI proposal, or a brownfield discovery import (spec §44). */
export const ARCHITECTURE_AUTHORS = ["user", "ai", "discovery"] as const;
export const ArchitectureAuthorSchema = z.enum(ARCHITECTURE_AUTHORS);

export const ArchitectureSchema = z.object({
  id: z.string(),
  projectId: z.string(),
  version: z.number().int().positive(),
  nodes: z.array(ArchitectureNodeSchema),
  edges: z.array(ArchitectureEdgeSchema),
  assumptions: z.array(AssumptionSchema),
  createdAt: z.string(),
  createdBy: ArchitectureAuthorSchema.default("user"),
});

export const ArchitectureVersionSummarySchema = z.object({
  version: z.number().int().positive(),
  createdAt: z.string(),
  createdBy: ArchitectureAuthorSchema,
  summary: z.string(),
});

export const ArchitectureVersionListSchema = z.array(ArchitectureVersionSummarySchema);

/** Visual layout is stored separately from semantic architecture (spec §67). */
export const LayoutUpdateSchema = z.object({
  positions: z.record(z.string(), PositionSchema),
});

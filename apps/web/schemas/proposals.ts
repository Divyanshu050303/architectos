/**
 * AI proposals (spec §31–33, §87–89). The AI never mutates the architecture: it returns
 * a proposal with an explicit diff that the user must approve.
 *
 * INTEGRATION POINT: proposed contract for POST /projects/{id}/proposals,
 * POST /projects/{id}/proposals/stream (NDJSON) and POST /proposals/{id}/apply|reject.
 */
import { z } from "zod";

import { ArchitectureEdgeSchema, ArchitectureNodeSchema } from "./architecture";
import { SeveritySchema } from "./validation";

export const ProposalChangeSchema = z.discriminatedUnion("op", [
  z.object({ op: z.literal("add_node"), node: ArchitectureNodeSchema }),
  z.object({ op: z.literal("remove_node"), nodeId: z.string(), name: z.string() }),
  z.object({
    op: z.literal("update_node"),
    nodeId: z.string(),
    name: z.string(),
    field: z.string(),
    before: z.unknown(),
    after: z.unknown(),
  }),
  z.object({
    op: z.literal("add_edge"),
    edge: ArchitectureEdgeSchema,
    sourceName: z.string(),
    targetName: z.string(),
  }),
  z.object({
    op: z.literal("remove_edge"),
    edgeId: z.string(),
    sourceName: z.string(),
    targetName: z.string(),
  }),
]);

export const ImpactSchema = z.object({
  metric: z.string(),
  before: z.number(),
  after: z.number(),
  unit: z.string(),
  evidenceId: z.string().nullable(),
});

/** A finding the proposed architecture would introduce (spec §89 "Validate" step). */
export const ProposalFindingSchema = z.object({
  severity: SeveritySchema,
  title: z.string(),
  location: z.string(),
});

/**
 * Backend validation of the proposed architecture, run before the user previews or
 * approves it (spec §89). Null when the proposal was not validated (e.g. answers).
 */
export const ProposalValidationSchema = z.object({
  summary: z.string(),
  newFindings: z.array(ProposalFindingSchema),
  /** Ids of current findings the change would resolve. */
  resolvedFindingIds: z.array(z.string()),
  passes: z.boolean(),
});

export const ProposalSchema = z.object({
  id: z.string(),
  projectId: z.string(),
  prompt: z.string(),
  baseVersion: z.number().int(),
  /** "change" proposals carry a diff; "answer" proposals only explain. */
  kind: z.enum(["change", "answer"]),
  recommendation: z.string(),
  reason: z.string(),
  impact: z.array(ImpactSchema),
  cost: z
    .object({ before: z.number(), after: z.number(), currency: z.string(), period: z.literal("month") })
    .nullable(),
  evidenceIds: z.array(z.string()),
  changes: z.array(ProposalChangeSchema),
  /** Defaults to null so proposals produced before validation existed still parse. */
  validation: ProposalValidationSchema.nullable().default(null),
  status: z.enum(["pending", "applied", "rejected"]),
});

export const ProposalRequestSchema = z.object({
  prompt: z.string().trim().min(1),
  baseVersion: z.number().int(),
  selectedNodeIds: z.array(z.string()),
});

// --- Streaming (spec §87–88) ------------------------------------------------

export const ProposalStepSchema = z.object({
  id: z.string(),
  label: z.string(),
  status: z.enum(["pending", "running", "done", "failed"]),
});

/**
 * One line of the NDJSON stream from POST /projects/{id}/proposals/stream.
 * Only progress and explanatory text are streamed; the proposal (and therefore any
 * architecture change) arrives once, complete, in the final "proposal" event (§88).
 */
export const ProposalStreamEventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("progress"), step: ProposalStepSchema }),
  z.object({ type: z.literal("text"), delta: z.string() }),
  z.object({ type: z.literal("proposal"), proposal: ProposalSchema }),
  z.object({ type: z.literal("error"), code: z.string(), message: z.string(), requestId: z.string() }),
]);

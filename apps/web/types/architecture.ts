import type { z } from "zod";

import type {
  ArchitectureAuthorSchema,
  ArchitectureEdgeSchema,
  ArchitectureNodeSchema,
  ArchitectureSchema,
  ArchitectureVersionSummarySchema,
  AssumptionSchema,
  PositionSchema,
} from "@/schemas/architecture";
import type { DecisionInputSchema, DecisionSchema } from "@/schemas/decisions";
import type { EvidenceSchema } from "@/schemas/evidence";
import type { ProposalChangeSchema, ProposalSchema } from "@/schemas/proposals";

export type Position = z.infer<typeof PositionSchema>;
export type ArchitectureNode = z.infer<typeof ArchitectureNodeSchema>;
export type ArchitectureEdge = z.infer<typeof ArchitectureEdgeSchema>;
export type Assumption = z.infer<typeof AssumptionSchema>;
export type Architecture = z.infer<typeof ArchitectureSchema>;
export type ArchitectureAuthor = z.infer<typeof ArchitectureAuthorSchema>;
export type ArchitectureVersionSummary = z.infer<typeof ArchitectureVersionSummarySchema>;

export type Proposal = z.infer<typeof ProposalSchema>;
export type ProposalChange = z.infer<typeof ProposalChangeSchema>;
export type Evidence = z.infer<typeof EvidenceSchema>;
export type Decision = z.infer<typeof DecisionSchema>;
export type DecisionInput = z.infer<typeof DecisionInputSchema>;

/** Canvas overlays: same graph, different lens (spec §68). */
export const ANALYSIS_MODES = [
  "topology",
  "capacity",
  "reliability",
  "security",
  "cost",
  "observability",
  "simulation",
] as const;
export type AnalysisMode = (typeof ANALYSIS_MODES)[number];

/** Modes the workspace can render. Every overlay now has backend data behind it (spec §68–72). */
export const V1_ANALYSIS_MODES: ReadonlySet<AnalysisMode> = new Set(ANALYSIS_MODES);

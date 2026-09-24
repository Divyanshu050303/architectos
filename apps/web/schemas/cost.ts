/**
 * Monthly cost estimate (spec §71). Every figure is computed by the backend cost engine
 * from provider price lists; the frontend only formats it.
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/cost.py.
 */
import { z } from "zod";

export const CloudProviderSchema = z.enum(["aws", "gcp", "azure"]);

export const CostLineItemSchema = z.object({
  item: z.string(),
  monthly: z.number().min(0),
});

export const NodeCostSchema = z.object({
  nodeId: z.string(),
  monthly: z.number().min(0),
  breakdown: z.array(CostLineItemSchema),
});

export const CostEstimateSchema = z.object({
  provider: CloudProviderSchema,
  /** ISO 4217, e.g. "USD" */
  currency: z.string(),
  period: z.literal("month"),
  total: z.number().min(0),
  nodes: z.array(NodeCostSchema),
  byCategory: z.array(z.object({ category: z.string(), monthly: z.number().min(0) })),
  assumptions: z.array(z.object({ id: z.string(), statement: z.string() })),
  evidenceIds: z.array(z.string()),
  calculatedAt: z.string(),
  architectureVersion: z.number().int(),
});

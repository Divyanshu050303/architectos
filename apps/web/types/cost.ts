import type { z } from "zod";

import type {
  CloudProviderSchema,
  CostEstimateSchema,
  CostLineItemSchema,
  NodeCostSchema,
} from "@/schemas/cost";

export type CostEstimate = z.infer<typeof CostEstimateSchema>;
export type NodeCost = z.infer<typeof NodeCostSchema>;
export type CostLineItem = z.infer<typeof CostLineItemSchema>;
export type CloudProvider = z.infer<typeof CloudProviderSchema>;

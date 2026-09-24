import type { z } from "zod";

import type {
  DiscoveredResourceSchema,
  DiscoveryConnectorKindSchema,
  DiscoveryConnectorSchema,
  DiscoveryOptionsSchema,
  DiscoveryRequestSchema,
  DiscoveryRunSchema,
} from "@/schemas/discovery";
import type { DriftItemSchema, DriftReportSchema, DriftStatusSchema } from "@/schemas/drift";

export type DiscoveryConnectorKind = z.infer<typeof DiscoveryConnectorKindSchema>;
export type DiscoveryConnector = z.infer<typeof DiscoveryConnectorSchema>;
export type DiscoveryOptions = z.infer<typeof DiscoveryOptionsSchema>;
export type DiscoveryRequest = z.infer<typeof DiscoveryRequestSchema>;
export type DiscoveredResource = z.infer<typeof DiscoveredResourceSchema>;
export type DiscoveryRun = z.infer<typeof DiscoveryRunSchema>;

export type DriftReport = z.infer<typeof DriftReportSchema>;
export type DriftItem = z.infer<typeof DriftItemSchema>;
export type DriftStatus = z.infer<typeof DriftStatusSchema>;

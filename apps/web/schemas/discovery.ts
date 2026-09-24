/**
 * Brownfield discovery (spec §44): connect → discover → normalize → generate → review →
 * save. Resource mapping and the proposed architecture come from the backend.
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/discovery.py.
 */
import { z } from "zod";

import { JobErrorSchema, JobStatusSchema, JobStepSchema } from "./api";
import { ArchitectureSchema, ComponentTypeSchema } from "./architecture";

export const DISCOVERY_CONNECTOR_KINDS = ["aws", "kubernetes", "terraform"] as const;
export const DiscoveryConnectorKindSchema = z.enum(DISCOVERY_CONNECTOR_KINDS);

export const DiscoveryConnectorSchema = z.object({
  kind: DiscoveryConnectorKindSchema,
  label: z.string(),
  description: z.string(),
  status: z.enum(["connected", "not_connected"]),
  /** e.g. "Account 1234•••• · us-east-1" */
  details: z.string().nullable(),
});

export const DiscoveryConnectorListSchema = z.array(DiscoveryConnectorSchema);

export const DiscoveryOptionsSchema = z.object({
  region: z.string().optional(),
  context: z.string().optional(),
  path: z.string().optional(),
});

export const DiscoveryRequestSchema = z.object({
  connector: DiscoveryConnectorKindSchema,
  options: DiscoveryOptionsSchema,
});

export const DiscoveredResourceSchema = z.object({
  id: z.string(),
  provider: DiscoveryConnectorKindSchema,
  /** Provider type, e.g. "aws_rds_instance" */
  resourceType: z.string(),
  name: z.string(),
  region: z.string().nullable(),
  mappedNodeType: ComponentTypeSchema.nullable(),
  status: z.enum(["mapped", "unmapped", "ignored"]),
});

export const DiscoveryRunSchema = z.object({
  id: z.string(),
  projectId: z.string(),
  connector: DiscoveryConnectorKindSchema,
  status: JobStatusSchema,
  steps: z.array(JobStepSchema),
  resources: z.array(DiscoveredResourceSchema),
  summary: z.object({
    total: z.number().int().min(0),
    mapped: z.number().int().min(0),
    unmapped: z.number().int().min(0),
    ignored: z.number().int().min(0),
  }),
  proposedArchitecture: ArchitectureSchema.nullable(),
  error: JobErrorSchema.nullable(),
});

export const DiscoverySaveRequestSchema = z.object({
  baseVersion: z.number().int().nullable(),
});

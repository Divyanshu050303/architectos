/**
 * Security posture (spec §37, §68): trust boundaries, exposure, per-component controls
 * and STRIDE threats. Produced by the backend security rules; the frontend only renders it.
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/security.py.
 */
import { z } from "zod";

import { SeveritySchema } from "./validation";

export const TrustBoundarySchema = z.object({
  id: z.string(),
  name: z.string(),
  nodeIds: z.array(z.string()),
});

export const EXPOSURE_LEVELS = ["public", "internal", "private"] as const;
export const ExposureLevelSchema = z.enum(EXPOSURE_LEVELS);

export const ExposureSchema = z.object({
  nodeId: z.string(),
  level: ExposureLevelSchema,
  reason: z.string(),
});

/** `null` means "unknown": the architecture does not say either way. */
export const SecurityControlsSchema = z.object({
  nodeId: z.string(),
  authentication: z.boolean().nullable(),
  authorization: z.boolean().nullable(),
  encryptionInTransit: z.boolean().nullable(),
  encryptionAtRest: z.boolean().nullable(),
  secretsManagement: z.boolean().nullable(),
  handlesPii: z.boolean(),
});

export const STRIDE_CATEGORIES = [
  "spoofing",
  "tampering",
  "repudiation",
  "information_disclosure",
  "denial_of_service",
  "elevation_of_privilege",
] as const;
export const StrideCategorySchema = z.enum(STRIDE_CATEGORIES);

export const ThreatSchema = z.object({
  id: z.string(),
  title: z.string(),
  category: StrideCategorySchema,
  severity: SeveritySchema,
  nodeIds: z.array(z.string()),
  mitigation: z.string(),
  evidenceId: z.string().nullable(),
});

export const SecurityAnalysisSchema = z.object({
  score: z.number().min(0).max(100),
  trustBoundaries: z.array(TrustBoundarySchema),
  exposure: z.array(ExposureSchema),
  controls: z.array(SecurityControlsSchema),
  threats: z.array(ThreatSchema),
  analyzedAt: z.string(),
  architectureVersion: z.number().int(),
});

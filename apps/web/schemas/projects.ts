/** INTEGRATION POINT: proposed contract for apps/api/routes/projects.py. */
import { z } from "zod";

export const HealthStatusSchema = z.enum(["healthy", "warning", "critical", "unknown"]);

export const ProjectSummarySchema = z.object({
  status: HealthStatusSchema,
  dailyActiveUsers: z.number().nullable(),
  peakRps: z.number().nullable(),
  /** Highest component utilisation, 0..1 */
  capacityUtilization: z.number().min(0).nullable(),
  topIssue: z.string().nullable(),
});

export const ProjectSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
  architectureVersion: z.number().int().nullable(),
  summary: ProjectSummarySchema,
});

export const ProjectListSchema = z.array(ProjectSchema);

export const ProjectInputSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(80, "Keep the name under 80 characters"),
  description: z.string().trim().max(500, "Keep the description under 500 characters"),
});

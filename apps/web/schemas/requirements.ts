/** INTEGRATION POINT: proposed contract for apps/api/routes/requirements.py. */
import { z } from "zod";

export const NonFunctionalRequirementsSchema = z.object({
  dailyActiveUsers: z.number().int().positive().nullable(),
  peakRps: z.number().positive().nullable(),
  /** e.g. 0.999 */
  availabilityTarget: z.number().gt(0).lt(1).nullable(),
  p99LatencyMs: z.number().positive().nullable(),
  dataRetentionDays: z.number().int().positive().nullable(),
  regions: z.array(z.string()),
});

export const RequirementsSchema = z.object({
  projectId: z.string(),
  description: z.string(),
  functional: z.array(z.string()),
  nonFunctional: NonFunctionalRequirementsSchema,
  updatedAt: z.string().nullable(),
});

/** Form input: strings from inputs, validated and converted before sending. */
const optionalPositiveNumber = (label: string) =>
  z
    .string()
    .trim()
    .transform((v) => (v === "" ? null : Number(v.replaceAll(",", ""))))
    .refine((v) => v === null || (Number.isFinite(v) && v > 0), `${label} must be a positive number`);

export const RequirementsFormSchema = z.object({
  description: z
    .string()
    .trim()
    .min(20, "Describe the system in at least a sentence or two (20+ characters)")
    .max(8000, "Keep the description under 8,000 characters"),
  functional: z.string(),
  dailyActiveUsers: optionalPositiveNumber("Daily active users"),
  peakRps: optionalPositiveNumber("Peak RPS"),
  availabilityTarget: z
    .string()
    .trim()
    .transform((v) => (v === "" ? null : Number(v) / 100))
    .refine((v) => v === null || (v > 0 && v < 1), "Availability must be between 0 and 100 (exclusive)"),
  p99LatencyMs: optionalPositiveNumber("P99 latency"),
  dataRetentionDays: optionalPositiveNumber("Retention"),
  regions: z.string(),
});

export type RequirementsFormInput = z.input<typeof RequirementsFormSchema>;

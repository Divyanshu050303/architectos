/**
 * Cross-cutting API envelopes.
 *
 * INTEGRATION POINT: the error envelope matches the shape planned for
 * apps/api/middleware/error_handler.py: {"error": {code, message, details, request_id}}.
 */
import { z } from "zod";

export const ApiErrorBodySchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.unknown().optional(),
    request_id: z.string().optional(),
  }),
});

export const JobStepSchema = z.object({
  id: z.string(),
  label: z.string(),
  status: z.enum(["pending", "running", "done", "failed"]),
});

/** Lifecycle shared by every long-running operation (jobs, simulations, discoveries). */
export const JobStatusSchema = z.enum(["queued", "running", "succeeded", "failed"]);

export const JobErrorSchema = z.object({ code: z.string(), message: z.string() });

/** Long-running AI operations are jobs with explicit steps (spec §48, §87). */
export const JobSchema = z.object({
  id: z.string(),
  kind: z.enum(["generate_architecture"]),
  status: JobStatusSchema,
  steps: z.array(JobStepSchema),
  result: z.object({ architectureVersion: z.number().int() }).nullable().default(null),
  error: z.object({ code: z.string(), message: z.string() }).nullable().default(null),
});

/**
 * Evidence behind a claim (spec §34). Evidence is always produced by the backend:
 * calculations, component constraints, rules or benchmarks.
 *
 * INTEGRATION POINT: proposed GET /evidence/{id} and GET /projects/{id}/evidence.
 */
import { z } from "zod";

export const EvidenceSchema = z.object({
  id: z.string(),
  claim: z.string(),
  kind: z.enum(["calculation", "constraint", "rule", "benchmark"]),
  calculations: z.array(
    z.object({
      label: z.string(),
      value: z.union([z.string(), z.number()]),
      unit: z.string().optional(),
    }),
  ),
  source: z.string(),
  assumptions: z.array(z.object({ id: z.string(), statement: z.string() })),
});

export const EvidenceListSchema = z.array(EvidenceSchema);

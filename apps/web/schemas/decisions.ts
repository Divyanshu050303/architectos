/** Architecture Decision Records. INTEGRATION POINT: proposed /projects/{id}/decisions. */
import { z } from "zod";

export const DecisionSchema = z.object({
  id: z.string(),
  number: z.number().int().positive(),
  title: z.string(),
  status: z.enum(["proposed", "accepted", "superseded", "rejected"]),
  date: z.string(),
  context: z.string(),
  decision: z.string(),
  consequences: z.string(),
  relatedNodeIds: z.array(z.string()),
  sourceFindingId: z.string().nullable(),
});

export const DecisionListSchema = z.array(DecisionSchema);

export const DecisionInputSchema = z.object({
  title: z.string().trim().min(3, "Title is required"),
  context: z.string().trim().min(1, "Context is required"),
  decision: z.string().trim().min(1, "Decision is required"),
  consequences: z.string().trim(),
  relatedNodeIds: z.array(z.string()),
  sourceFindingId: z.string().nullable(),
});

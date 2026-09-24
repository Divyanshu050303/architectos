import type { z } from "zod";

import type { JobSchema } from "@/schemas/api";
import type { ProjectInputSchema, ProjectSchema } from "@/schemas/projects";
import type { RequirementsSchema } from "@/schemas/requirements";

export type Project = z.infer<typeof ProjectSchema>;
export type ProjectInput = z.infer<typeof ProjectInputSchema>;
export type Requirements = z.infer<typeof RequirementsSchema>;
export type Job = z.infer<typeof JobSchema>;

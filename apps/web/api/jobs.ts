/**
 * INTEGRATION POINT: proposed contract for long-running jobs (spec §48, §87).
 *   GET /jobs/{jobId}
 */
import { JobSchema } from "@/schemas/api";
import type { Job } from "@/types/project";

import { apiPath, request } from "./client";

export function getJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  return request(JobSchema, { method: "GET", path: apiPath`/jobs/${jobId}`, signal });
}

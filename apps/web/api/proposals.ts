/**
 * INTEGRATION POINT: proposed contract for AI proposals (spec §31–33, §87–89). The AI
 * never mutates the architecture; applying a proposal creates a new version.
 *   POST /projects/{id}/proposals               { prompt, baseVersion, selectedNodeIds } → Proposal
 *   POST /projects/{id}/proposals/stream        same body → application/x-ndjson, one
 *        ProposalStreamEvent per line (schemas/proposals.ts):
 *          {"type":"progress","step":{"id","label","status"}}   real pipeline steps (§87)
 *          {"type":"text","delta":"…"}                          explanatory text only (§88)
 *          {"type":"proposal","proposal":Proposal}              final and complete, sent once
 *          {"type":"error","code","message","requestId"}        terminal failure
 *        Errors before streaming starts (404 project, 409 version_conflict, 422) use the
 *        normal JSON error envelope. Architecture changes are never streamed partially.
 *        A 404 "not_found"/405 (endpoint not deployed) falls back to the endpoint above.
 *   POST /projects/{id}/findings/{findingId}/fix                                         → Proposal
 *   POST /proposals/{id}/apply                  { baseVersion } → Architecture; 409 "version_conflict"
 *   POST /proposals/{id}/reject                                                          → Proposal
 * Every Proposal carries `validation` (§89): the backend validates the proposed
 * architecture before the user previews or approves it.
 */
import type { z } from "zod";

import { ArchitectureSchema } from "@/schemas/architecture";
import {
  type ProposalFindingSchema,
  type ProposalRequestSchema,
  ProposalSchema,
  type ProposalStepSchema,
  ProposalStreamEventSchema,
  type ProposalValidationSchema,
} from "@/schemas/proposals";
import type { Architecture, Proposal } from "@/types/architecture";

import { ApiError, apiPath, request, requestStream } from "./client";

export type ProposalRequest = z.infer<typeof ProposalRequestSchema>;
export type ProposalStep = z.infer<typeof ProposalStepSchema>;
export type ProposalStreamEvent = z.infer<typeof ProposalStreamEventSchema>;
export type ProposalValidation = z.infer<typeof ProposalValidationSchema>;
export type ProposalFinding = z.infer<typeof ProposalFindingSchema>;

/** AI calls can take a while; the default 15s timeout is too short. */
const AI_TIMEOUT_MS = 60_000;
/** Streams report progress, so the timeout bounds the silence between events instead. */
const AI_STREAM_IDLE_TIMEOUT_MS = 30_000;

export function createProposal(
  projectId: string,
  input: ProposalRequest,
  signal?: AbortSignal,
): Promise<Proposal> {
  return request(ProposalSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/proposals`,
    body: input,
    timeoutMs: AI_TIMEOUT_MS,
    signal,
  });
}

export interface StreamProposalHandlers {
  signal?: AbortSignal;
  /** A pipeline step changed status. */
  onProgress?: (step: ProposalStep) => void;
  /** A delta of explanatory text (never architecture changes). */
  onText?: (delta: string) => void;
}

/** The streaming endpoint is not deployed (as opposed to e.g. an unknown project). */
function isMissingEndpoint(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 405) return true;
  return error.status === 404 && (error.code === "not_found" || error.code === "http_404");
}

/** Non-streaming fallback still shows honest progress: one step, done on response. */
const FALLBACK_STEP = { id: "request", label: "Drafting proposal" } as const;

async function createWithoutStreaming(
  projectId: string,
  input: ProposalRequest,
  handlers: StreamProposalHandlers,
): Promise<Proposal> {
  handlers.onProgress?.({ ...FALLBACK_STEP, status: "running" });
  const proposal = await createProposal(projectId, input, handlers.signal);
  handlers.onProgress?.({ ...FALLBACK_STEP, status: "done" });
  return proposal;
}

/**
 * Request a proposal over the NDJSON stream, reporting progress and explanatory text as
 * they arrive. Resolves with the final proposal, which is delivered atomically (§88).
 */
export async function streamProposal(
  projectId: string,
  input: ProposalRequest,
  handlers: StreamProposalHandlers = {},
): Promise<Proposal> {
  let requestId: string | null = null;
  const events = requestStream(
    ProposalStreamEventSchema,
    {
      method: "POST",
      path: apiPath`/projects/${projectId}/proposals/stream`,
      body: input,
      timeoutMs: AI_STREAM_IDLE_TIMEOUT_MS,
      signal: handlers.signal,
    },
    (id) => {
      requestId = id;
    },
  );
  let received = false;
  try {
    for await (const event of events) {
      received = true;
      switch (event.type) {
        case "progress":
          handlers.onProgress?.(event.step);
          break;
        case "text":
          handlers.onText?.(event.delta);
          break;
        case "proposal":
          // Final and complete: stop reading (closing the stream) and hand it over whole.
          return event.proposal;
        case "error":
          throw new ApiError({
            status: 0,
            code: event.code,
            message: event.message,
            requestId: event.requestId,
          });
      }
    }
  } catch (error) {
    if (!received && isMissingEndpoint(error)) return createWithoutStreaming(projectId, input, handlers);
    throw error;
  }
  throw new ApiError({
    status: 0,
    code: "invalid_response",
    message: "The response ended before a proposal was produced.",
    requestId: requestId ?? "unknown",
  });
}

export function fixFinding(projectId: string, findingId: string): Promise<Proposal> {
  return request(ProposalSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/findings/${findingId}/fix`,
    timeoutMs: AI_TIMEOUT_MS,
  });
}

export function applyProposal(proposalId: string, baseVersion: number): Promise<Architecture> {
  return request(ArchitectureSchema, {
    method: "POST",
    path: apiPath`/proposals/${proposalId}/apply`,
    body: { baseVersion },
  });
}

export function rejectProposal(proposalId: string): Promise<Proposal> {
  return request(ProposalSchema, { method: "POST", path: apiPath`/proposals/${proposalId}/reject` });
}

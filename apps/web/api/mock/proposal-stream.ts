/**
 * MOCK STREAMING PROPOSALS — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Serves POST /projects/{id}/proposals/stream as NDJSON (spec §87–88). The proposal is
 * produced by the ordinary mock proposal route (keyword-driven FIXTURE logic, not real
 * AI); this module only paces it as progress steps and chunked explanatory text, then
 * emits the complete proposal atomically. The pacing is illustrative, not real work.
 */
import type { z } from "zod";

import { ProposalSchema, type ProposalStreamEventSchema } from "@/schemas/proposals";
import type { Proposal } from "@/types/architecture";

import { handleMockRequest } from "./handlers";
import type { MockRequest, MockResponse } from "./router";

type StreamEvent = z.infer<typeof ProposalStreamEventSchema>;

const STREAM_PATH = /^\/projects\/([^/]+)\/proposals\/stream$/;

/** Delay between events outside tests, so progress is visible in dev. */
const EVENT_DELAY_MS = 180;
const TEXT_CHUNK_WORDS = 3;

export type MockStreamResponse =
  | { kind: "stream"; status: number; stream: ReadableStream<Uint8Array> }
  | { kind: "response"; response: MockResponse };

type StepStatus = Extract<StreamEvent, { type: "progress" }>["step"]["status"];

function progress(id: string, label: string, status: StepStatus): StreamEvent {
  return { type: "progress", step: { id, label, status } };
}

/** Split explanatory text into small deltas, as a model would stream it. */
export function chunkText(text: string, wordsPerChunk = TEXT_CHUNK_WORDS): string[] {
  const words = text.split(/(?<=\s)/);
  const chunks: string[] = [];
  for (let i = 0; i < words.length; i += wordsPerChunk) {
    chunks.push(words.slice(i, i + wordsPerChunk).join(""));
  }
  return chunks;
}

/**
 * The mock event sequence for a finished (fixture) proposal: every step announced as
 * pending, then run in order; explanatory text streams while drafting; the proposal last.
 */
export function mockProposalEvents(proposal: Proposal): StreamEvent[] {
  const steps: ReadonlyArray<readonly [id: string, label: string]> = [
    ["understand", "Understanding request"],
    ["draft", proposal.kind === "change" ? "Drafting proposal" : "Drafting answer"],
    ...(proposal.kind === "change"
      ? ([
          ["validate", "Validating proposal"],
          ["impact", "Checking impact"],
        ] as const)
      : []),
  ];
  const text = chunkText(`${proposal.recommendation} ${proposal.reason}`).map((delta): StreamEvent => ({
    type: "text",
    delta,
  }));
  return [
    ...steps.map(([id, label]) => progress(id, label, "pending")),
    ...steps.flatMap(([id, label]) => [
      progress(id, label, "running"),
      ...(id === "draft" ? text : []),
      progress(id, label, "done"),
    ]),
    { type: "proposal", proposal },
  ];
}

function delay(ms: number): Promise<void> {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}

/** Encode events as an NDJSON byte stream that stops when cancelled. */
export function ndjsonStream(events: readonly StreamEvent[], delayMs: number): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  let cancelled = false;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (index > 0) await delay(delayMs);
      if (cancelled) return;
      const event = events[index];
      if (event === undefined) {
        controller.close();
        return;
      }
      index += 1;
      controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
    },
    cancel() {
      cancelled = true;
    },
  });
}

/**
 * Handle a streaming request, or return null when `request` is not a mock stream route
 * (the transport then answers it like any other request, e.g. with a 404).
 */
export function handleMockStreamRequest(request: MockRequest, requestId: string): MockStreamResponse | null {
  const match = STREAM_PATH.exec(request.path);
  if (!match || request.method !== "POST") return null;
  const projectId = decodeURIComponent(match[1] ?? "");
  // Reuse the non-streaming mock route: same validation, version check and fixture logic.
  const created = handleMockRequest(
    { method: "POST", path: `/projects/${encodeURIComponent(projectId)}/proposals`, body: request.body },
    requestId,
  );
  if (created.status !== 201) return { kind: "response", response: created };
  // Round-trip through JSON so the stream never shares references with the mock db.
  const proposal = ProposalSchema.parse(JSON.parse(JSON.stringify(created.body)));
  const delayMs = process.env.NODE_ENV === "test" ? 0 : EVENT_DELAY_MS;
  return { kind: "stream", status: 200, stream: ndjsonStream(mockProposalEvents(proposal), delayMs) };
}

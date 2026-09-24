import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import {
  ApiError,
  parseNdjson,
  requestStream,
  setTransport,
  type Transport,
  type TransportRequest,
} from "@/api/client";
import { streamProposal } from "@/api/proposals";
import type { ProposalStreamEventSchema } from "@/schemas/proposals";
import type { Proposal } from "@/types/architecture";

const EventSchema = z.object({ n: z.number() });

/** A byte stream that delivers exactly these chunks (split anywhere, even mid-character). */
function byteStream(chunks: ReadonlyArray<string | Uint8Array>): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks[i];
      i += 1;
      if (chunk === undefined) controller.close();
      else controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
    },
  });
}

async function collect<T>(iterable: AsyncIterable<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of iterable) out.push(item);
  return out;
}

async function captureError(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the promise to reject");
}

const headers = (values: Record<string, string> = {}) => ({ get: (name: string) => values[name] ?? null });

function streamingTransport(stream: ReadableStream<Uint8Array>, seen?: TransportRequest[]): Transport {
  return async (request) => {
    seen?.push(request);
    return { status: 200, body: null, headers: headers({ "x-request-id": "req_srv" }), stream };
  };
}

afterEach(() => setTransport(null));

describe("parseNdjson", () => {
  it("reassembles lines split across chunk boundaries and skips blank lines", async () => {
    const stream = byteStream(['{"n":', '1}\n\n{"n"', ':2}\n{"n":3', "}"]);
    const events = await collect(parseNdjson(stream, EventSchema, { requestId: "req_1" }));
    expect(events).toEqual([{ n: 1 }, { n: 2 }, { n: 3 }]);
  });

  it("decodes multi-byte characters split between chunks", async () => {
    const bytes = new TextEncoder().encode('{"n":1,"s":"→"}\n');
    const cut = bytes.indexOf(0xe2) + 1; // inside the 3-byte arrow
    const schema = z.object({ n: z.number(), s: z.string() });
    const events = await collect(
      parseNdjson(byteStream([bytes.slice(0, cut), bytes.slice(cut)]), schema, { requestId: "r" }),
    );
    expect(events).toEqual([{ n: 1, s: "→" }]);
  });

  it("turns a malformed line into a normalized invalid_response error", async () => {
    const stream = byteStream(['{"n":1}\n', "not json\n", '{"n":3}\n']);
    const seen: unknown[] = [];
    const error = await captureError(
      (async () => {
        for await (const event of parseNdjson(stream, EventSchema, { requestId: "req_bad" }))
          seen.push(event);
      })(),
    );
    expect(seen).toEqual([{ n: 1 }]);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "invalid_response", requestId: "req_bad" });
    expect((error as ApiError).message).toMatch(/line 2/);
  });

  it("rejects lines that do not match the schema", async () => {
    const error = await captureError(
      collect(parseNdjson(byteStream(['{"n":"one"}\n']), EventSchema, { requestId: "r" })),
    );
    expect(error).toMatchObject({ code: "invalid_response" });
  });

  it("cancels the underlying stream when the consumer stops early", async () => {
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new TextEncoder().encode('{"n":1}\n'));
      },
      cancel,
    });
    for await (const event of parseNdjson(stream, EventSchema, { requestId: "r" })) {
      expect(event).toEqual({ n: 1 });
      break;
    }
    expect(cancel).toHaveBeenCalled();
  });
});

describe("requestStream", () => {
  it("sends the standard headers and yields validated events", async () => {
    const seen: TransportRequest[] = [];
    setTransport(streamingTransport(byteStream(['{"n":1}\n{"n":2}\n']), seen));
    const events = await collect(
      requestStream(EventSchema, { method: "POST", path: "/things/stream", body: { a: 1 } }),
    );
    expect(events).toEqual([{ n: 1 }, { n: 2 }]);
    expect(seen[0]).toMatchObject({ method: "POST", stream: true, body: '{"a":1}' });
    expect(seen[0]?.headers).toMatchObject({
      Accept: "application/x-ndjson",
      "Content-Type": "application/json",
      "X-Request-ID": expect.any(String),
    });
  });

  it("normalizes HTTP errors from the error envelope", async () => {
    setTransport(async () => ({
      status: 409,
      body: { error: { code: "version_conflict", message: "Changed.", request_id: "req_409" } },
      headers: headers(),
    }));
    const error = await captureError(collect(requestStream(EventSchema, { method: "POST", path: "/x" })));
    expect(error).toMatchObject({ status: 409, code: "version_conflict", requestId: "req_409" });
  });

  it("uses the server request id for invalid lines", async () => {
    setTransport(streamingTransport(byteStream(["{oops}\n"])));
    const error = await captureError(collect(requestStream(EventSchema, { method: "POST", path: "/x" })));
    expect(error).toMatchObject({ code: "invalid_response", requestId: "req_srv" });
  });

  it("aborts cleanly: rethrows the caller's reason and cancels the stream", async () => {
    const cancel = vi.fn();
    const encoder = new TextEncoder();
    let sent = false;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (!sent) controller.enqueue(encoder.encode('{"n":1}\n'));
        sent = true;
        return new Promise(() => undefined); // then hang until cancelled
      },
      cancel,
    });
    setTransport(streamingTransport(stream));
    const controller = new AbortController();
    const reason = new DOMException("Stopped", "AbortError");
    const seen: unknown[] = [];
    const error = await captureError(
      (async () => {
        for await (const event of requestStream(EventSchema, {
          method: "POST",
          path: "/x",
          signal: controller.signal,
        })) {
          seen.push(event);
          controller.abort(reason);
        }
      })(),
    );
    expect(seen).toEqual([{ n: 1 }]);
    expect(error).toBe(reason);
    expect(cancel).toHaveBeenCalled();
  });

  it("times out when the stream goes idle", async () => {
    const stream = new ReadableStream<Uint8Array>({ pull: () => new Promise(() => undefined) });
    setTransport(streamingTransport(stream));
    const error = await captureError(
      collect(requestStream(EventSchema, { method: "POST", path: "/x", timeoutMs: 20 })),
    );
    expect(error).toMatchObject({ code: "timeout" });
  });
});

describe("streamProposal", () => {
  const proposal: Proposal = {
    id: "prop_1",
    projectId: "proj_1",
    prompt: "Explain",
    baseVersion: 1,
    kind: "answer",
    recommendation: "R",
    reason: "W",
    impact: [],
    cost: null,
    evidenceIds: [],
    changes: [],
    validation: null,
    status: "pending",
  };
  const input = { prompt: "Explain", baseVersion: 1, selectedNodeIds: [] };
  const line = (event: z.input<typeof ProposalStreamEventSchema>) => `${JSON.stringify(event)}\n`;

  it("reports progress and text, then resolves with the final proposal", async () => {
    setTransport(
      streamingTransport(
        byteStream([
          line({ type: "progress", step: { id: "a", label: "A", status: "running" } }),
          line({ type: "text", delta: "Hello " }),
          line({ type: "text", delta: "world" }),
          line({ type: "proposal", proposal }),
        ]),
      ),
    );
    const onProgress = vi.fn();
    const onText = vi.fn();
    await expect(streamProposal("proj_1", input, { onProgress, onText })).resolves.toEqual(proposal);
    expect(onProgress).toHaveBeenCalledWith({ id: "a", label: "A", status: "running" });
    expect(onText.mock.calls.map((c) => c[0])).toEqual(["Hello ", "world"]);
  });

  it("throws in-stream error events as ApiErrors", async () => {
    setTransport(
      streamingTransport(
        byteStream([line({ type: "error", code: "model_overloaded", message: "Busy.", requestId: "req_e" })]),
      ),
    );
    const error = await captureError(streamProposal("proj_1", input));
    expect(error).toMatchObject({ code: "model_overloaded", message: "Busy.", requestId: "req_e" });
  });

  it("fails when the stream ends without a proposal", async () => {
    setTransport(streamingTransport(byteStream([line({ type: "text", delta: "…" })])));
    const error = await captureError(streamProposal("proj_1", input));
    expect(error).toMatchObject({ code: "invalid_response", requestId: "req_srv" });
  });

  it.each([
    [404, "not_found"],
    [405, "method_not_allowed"],
  ])("falls back to the non-streaming endpoint on %i", async (status, code) => {
    const paths: string[] = [];
    setTransport(async (request) => {
      paths.push(new URL(request.url).pathname);
      if (request.stream) {
        return { status, body: { error: { code, message: "No route." } }, headers: headers() };
      }
      return { status: 201, body: proposal, headers: headers() };
    });
    const onProgress = vi.fn();
    await expect(streamProposal("proj_1", input, { onProgress })).resolves.toEqual(proposal);
    expect(paths.map((p) => p.replace(/^.*\/projects/, "/projects"))).toEqual([
      "/projects/proj_1/proposals/stream",
      "/projects/proj_1/proposals",
    ]);
    expect(onProgress.mock.calls.map((c) => (c[0] as { status: string }).status)).toEqual([
      "running",
      "done",
    ]);
  });

  it("does not fall back when the project itself is missing", async () => {
    setTransport(async () => ({
      status: 404,
      body: { error: { code: "project_not_found", message: "Nope." } },
      headers: headers(),
    }));
    const error = await captureError(streamProposal("proj_1", input));
    expect(error).toMatchObject({ status: 404, code: "project_not_found" });
  });
});

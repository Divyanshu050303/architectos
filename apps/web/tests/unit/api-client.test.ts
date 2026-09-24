import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import {
  ApiError,
  getErrorInfo,
  request,
  requestVoid,
  setTransport,
  type Transport,
  type TransportRequest,
} from "@/api/client";

const ItemSchema = z.object({ id: z.string(), count: z.number() });

function respond(status: number, body: unknown, requestId?: string) {
  return {
    status,
    body,
    headers: { get: (name: string) => (name.toLowerCase() === "x-request-id" ? (requestId ?? null) : null) },
  };
}

function recordingTransport(...responses: Array<ReturnType<typeof respond> | Error>) {
  const calls: TransportRequest[] = [];
  const transport: Transport = async (req) => {
    calls.push(req);
    const next = responses[Math.min(calls.length - 1, responses.length - 1)];
    if (!next) throw new Error("no response configured");
    if (next instanceof Error) throw next;
    return next;
  };
  return { transport, calls };
}

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("expected the request to fail");
}

afterEach(() => setTransport(null));

describe("request", () => {
  it("parses a successful response and sends JSON headers with a request id", async () => {
    const { transport, calls } = recordingTransport(respond(200, { id: "a", count: 2 }));
    setTransport(transport);

    const data = await request(ItemSchema, {
      method: "POST",
      path: "/items",
      body: { name: "x" },
      query: { page: 2, skip: undefined },
    });

    expect(data).toEqual({ id: "a", count: 2 });
    const call = calls[0];
    expect(call?.url).toMatch(/\/items\?page=2$/);
    expect(call?.headers["Content-Type"]).toBe("application/json");
    expect(call?.headers["X-Request-ID"]).toMatch(/[0-9a-f-]{36}/);
    expect(call?.body).toBe(JSON.stringify({ name: "x" }));
  });

  it("normalizes the error envelope, keeping the server request id", async () => {
    setTransport(
      recordingTransport(
        respond(409, {
          error: {
            code: "version_conflict",
            message: "Stale version",
            details: { latestVersion: 4 },
            request_id: "req_8d2f",
          },
        }),
      ).transport,
    );

    const error = await captureError(request(ItemSchema, { method: "POST", path: "/items" }));
    expect(error).toMatchObject({
      status: 409,
      code: "version_conflict",
      message: "Stale version",
      requestId: "req_8d2f",
      details: { latestVersion: 4 },
    });
    expect(getErrorInfo(error)).toMatchObject({ title: "Architecture updated", requestId: "req_8d2f" });
  });

  it("falls back to the response header request id for non-envelope errors", async () => {
    setTransport(recordingTransport(respond(500, "oops", "req_header")).transport);
    const error = await captureError(request(ItemSchema, { method: "POST", path: "/items" }));
    expect(error).toMatchObject({ status: 500, code: "http_500", requestId: "req_header" });
    expect(getErrorInfo(error).title).toBe("Server error");
  });

  it("rejects responses that do not match the schema", async () => {
    setTransport(recordingTransport(respond(200, { id: 1 })).transport);
    const error = await captureError(request(ItemSchema, { method: "GET", path: "/items/1" }));
    expect(error.code).toBe("invalid_response");
    expect(error.message).toBe("The server response did not match the expected schema.");
    expect(Array.isArray(error.details)).toBe(true);
    expect(error.requestId).toBeTruthy();
  });

  it("retries GET on 503 and then succeeds", async () => {
    const unavailable = respond(503, { error: { code: "unavailable", message: "Try again" } });
    const { transport, calls } = recordingTransport(unavailable, respond(200, { id: "a", count: 1 }));
    setTransport(transport);

    await expect(request(ItemSchema, { method: "GET", path: "/items/a" })).resolves.toEqual({
      id: "a",
      count: 1,
    });
    expect(calls).toHaveLength(2);
  });

  it("retries network errors on GET and reports network_error when exhausted", async () => {
    const { transport, calls } = recordingTransport(new TypeError("Failed to fetch"));
    setTransport(transport);
    const error = await captureError(request(ItemSchema, { method: "GET", path: "/items", retries: 1 }));
    expect(error.code).toBe("network_error");
    expect(error.retryable).toBe(true);
    expect(calls).toHaveLength(2);
  });

  it("does not retry POST", async () => {
    const { transport, calls } = recordingTransport(
      respond(503, { error: { code: "unavailable", message: "Try again" } }),
    );
    setTransport(transport);
    const error = await captureError(request(ItemSchema, { method: "POST", path: "/items" }));
    expect(error.status).toBe(503);
    expect(calls).toHaveLength(1);
  });

  it("times out with code timeout", async () => {
    const hanging: Transport = (req) =>
      new Promise((_resolve, reject) => {
        req.signal.addEventListener("abort", () => reject(req.signal.reason));
      });
    setTransport(hanging);
    const error = await captureError(
      request(ItemSchema, { method: "GET", path: "/slow", timeoutMs: 20, retries: 0 }),
    );
    expect(error.code).toBe("timeout");
    expect(getErrorInfo(error).title).toBe("The request timed out");
  });

  it("propagates caller aborts without retrying or wrapping", async () => {
    const calls = vi.fn();
    const hanging: Transport = (req) =>
      new Promise((_resolve, reject) => {
        calls();
        if (req.signal.aborted) reject(new TypeError("aborted"));
        req.signal.addEventListener("abort", () => reject(new TypeError("aborted")));
      });
    setTransport(hanging);
    const controller = new AbortController();
    const promise = request(ItemSchema, { method: "GET", path: "/slow", signal: controller.signal });
    controller.abort();
    await expect(promise).rejects.not.toBeInstanceOf(ApiError);
    expect(calls).toHaveBeenCalledTimes(1);
  });

  it("requestVoid accepts 204", async () => {
    setTransport(recordingTransport(respond(204, null)).transport);
    await expect(requestVoid({ method: "PUT", path: "/layout", body: {} })).resolves.toBeUndefined();
  });
});

describe("getErrorInfo", () => {
  it("handles non-API errors", () => {
    expect(getErrorInfo(new Error("boom"))).toEqual({
      title: "Unexpected error",
      message: "boom",
      code: "unknown",
    });
    expect(getErrorInfo("x").code).toBe("unknown");
  });
});

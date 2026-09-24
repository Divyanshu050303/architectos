/**
 * MOCK TRANSPORT — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Stands in for fetch: routes requests to the in-memory mock backend, which serves
 * fixture data, not real analysis. Loaded lazily by api/client.ts only in mock mode.
 */
import { config } from "@/config/env";
import { createId } from "@/lib/utils";

import type { Transport } from "../client";
import { handleMockRequest } from "./handlers";
import { handleMockStreamRequest } from "./proposal-stream";

const MIN_LATENCY_MS = 120;
const MAX_LATENCY_MS = 350;

function latency(signal: AbortSignal): Promise<void> {
  const ms =
    process.env.NODE_ENV === "test"
      ? 0
      : MIN_LATENCY_MS + Math.round(Math.random() * (MAX_LATENCY_MS - MIN_LATENCY_MS));
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(signal.reason);
    }
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function relativePath(url: string): string {
  const { pathname } = new URL(url);
  const basePath = new URL(config.apiUrl).pathname.replace(/\/$/, "");
  return basePath && pathname.startsWith(basePath) ? pathname.slice(basePath.length) || "/" : pathname;
}

function headers(requestId: string) {
  const values: Record<string, string> = { "x-request-id": requestId, "content-type": "application/json" };
  return { get: (name: string) => values[name.toLowerCase()] ?? null };
}

export const mockTransport: Transport = async (request) => {
  const requestId = request.headers["X-Request-ID"] ?? createId("req_mock");
  await latency(request.signal);

  let body: unknown;
  try {
    body = request.body === undefined ? undefined : JSON.parse(request.body);
  } catch {
    return {
      status: 400,
      body: {
        error: { code: "invalid_json", message: "Request body is not valid JSON.", request_id: requestId },
      },
      headers: headers(requestId),
    };
  }

  const mockRequest = {
    method: request.method,
    path: relativePath(request.url),
    body,
    query: new URL(request.url).searchParams,
  };
  // Streaming (NDJSON) endpoints; anything else falls through to the JSON routes.
  const streamed = request.stream ? handleMockStreamRequest(mockRequest, requestId) : null;
  if (streamed?.kind === "stream") {
    return { status: streamed.status, body: null, headers: headers(requestId), stream: streamed.stream };
  }
  const response = streamed?.response ?? handleMockRequest(mockRequest, requestId);
  return {
    status: response.status,
    // Round-trip through JSON so callers never share references with the mock db.
    body: response.body === null ? null : (JSON.parse(JSON.stringify(response.body)) as unknown),
    headers: headers(requestId),
  };
};

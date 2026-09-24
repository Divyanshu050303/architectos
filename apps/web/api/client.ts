/**
 * Centralized API client (spec §54–55): base URL, auth, headers, request IDs,
 * timeouts, retries, error normalization and Zod validation of every response.
 *
 * INTEGRATION POINT: apps/api has no implementation yet. Paths, the error envelope
 * (schemas/api.ts) and auth are the frontend's proposed contract.
 */
import { type z } from "zod";

import { config } from "@/config/env";
import { logger } from "@/lib/logger";
import { ApiErrorBodySchema } from "@/schemas/api";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface TransportRequest {
  method: HttpMethod;
  url: string;
  headers: Record<string, string>;
  body?: string;
  signal: AbortSignal;
  /** Streaming endpoints (NDJSON): return a successful body unread, as `stream`. */
  stream?: boolean;
}

export interface TransportResponse {
  status: number;
  /** Parsed body. For streaming requests only error (non-2xx) bodies are read. */
  body: unknown;
  headers: { get(name: string): string | null };
  /** The unread body of a successful streaming response. */
  stream?: ReadableStream<Uint8Array> | null;
}

export type Transport = (request: TransportRequest) => Promise<TransportResponse>;

export interface ApiRequest {
  method: HttpMethod;
  /** Relative to the API base URL, e.g. "/projects/abc". Build with `apiPath`. */
  path: string;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Defaults to 2 for GET and 0 otherwise: only idempotent reads are retried. */
  retries?: number;
}

interface ApiErrorInit {
  status: number;
  code: string;
  message: string;
  requestId: string;
  details?: unknown;
}

const RETRYABLE_STATUSES: ReadonlySet<number> = new Set([429, 502, 503, 504]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly details?: unknown;

  constructor(init: ApiErrorInit) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId;
    this.details = init.details;
  }

  /** Transient failures that are safe to retry for idempotent requests. */
  get retryable(): boolean {
    return this.code === "network_error" || RETRYABLE_STATUSES.has(this.status);
  }
}

export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code);
}

/** Tagged template that URL-encodes every interpolated segment. */
export function apiPath(strings: TemplateStringsArray, ...values: Array<string | number>): string {
  return strings.reduce(
    (path, part, i) => path + part + (i < values.length ? encodeURIComponent(String(values[i])) : ""),
    "",
  );
}

// --- Auth -------------------------------------------------------------------

type AccessTokenProvider = () => string | null | Promise<string | null>;

/** INTEGRATION POINT: the auth mechanism is not decided; no token is sent by default. */
let accessTokenProvider: AccessTokenProvider | null = null;

export function setAccessTokenProvider(provider: AccessTokenProvider | null): void {
  accessTokenProvider = provider;
}

// --- Transport --------------------------------------------------------------

const fetchTransport: Transport = async ({ method, url, headers, body, signal, stream }) => {
  // The session is an httpOnly cookie on the API origin (api/auth.ts), so it must be sent.
  const response = await fetch(url, { method, headers, body, signal, credentials: "include" });
  if (stream && response.ok) {
    return { status: response.status, body: null, headers: response.headers, stream: response.body };
  }
  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  return { status: response.status, body: parsed, headers: response.headers };
};

let transportOverride: Transport | null = null;
let mockTransport: Promise<Transport> | null = null;

/** Replace the transport (tests). Pass null to restore the default. */
export function setTransport(transport: Transport | null): void {
  transportOverride = transport;
}

function resolveTransport(): Transport | Promise<Transport> {
  if (transportOverride) return transportOverride;
  if (config.useMocks) {
    mockTransport ??= import("./mock/transport").then((m) => m.mockTransport);
    return mockTransport;
  }
  return fetchTransport;
}

// --- Request pipeline -------------------------------------------------------

const DEFAULT_TIMEOUT_MS = 15_000;
const RETRY_BASE_DELAY_MS = 300;

function buildUrl(path: string, query: ApiRequest["query"]): string {
  const url = `${config.apiUrl}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const search = params.toString();
  return search ? `${url}?${search}` : url;
}

function toApiError(status: number, body: unknown, requestId: string): ApiError {
  const parsed = ApiErrorBodySchema.safeParse(body);
  if (parsed.success) {
    const { code, message, details, request_id } = parsed.data.error;
    return new ApiError({ status, code, message, details, requestId: request_id ?? requestId });
  }
  return new ApiError({
    status,
    code: `http_${status}`,
    message: `The server responded with status ${status}.`,
    requestId,
  });
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(signal?.reason);
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

interface SendOptions {
  /** Signal combining the caller's signal with the timeout. */
  signal: AbortSignal;
  /** Aborted when the request timed out (distinguishes timeouts from cancels). */
  timeoutSignal: AbortSignal;
  timeoutMs: number;
  stream?: boolean;
}

async function buildHeaders(
  req: ApiRequest,
  requestId: string,
  accept: string,
): Promise<Record<string, string>> {
  const headers: Record<string, string> = { Accept: accept, "X-Request-ID": requestId };
  if (req.body !== undefined) headers["Content-Type"] = "application/json";
  const token = accessTokenProvider ? await accessTokenProvider() : null;
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/** Map a thrown transport/stream failure to the normalized error (or rethrow a cancel). */
function normalizeFailure(error: unknown, req: ApiRequest, options: SendOptions, requestId: string): never {
  // Caller cancellations propagate untouched so TanStack Query treats them as cancels.
  if (req.signal?.aborted) throw req.signal.reason ?? error;
  if (error instanceof ApiError) throw error;
  if (options.timeoutSignal.aborted) {
    throw new ApiError({
      status: 0,
      code: "timeout",
      message: `The server did not respond within ${Math.round(options.timeoutMs / 1000)} seconds.`,
      requestId,
    });
  }
  throw new ApiError({
    status: 0,
    code: "network_error",
    message: "Could not reach the ArchitectOS API. Check your connection and try again.",
    requestId,
    details: error instanceof Error ? error.message : String(error),
  });
}

async function sendWith(
  req: ApiRequest,
  requestId: string,
  options: SendOptions,
): Promise<TransportResponse> {
  const accept = options.stream ? "application/x-ndjson" : "application/json";
  const headers = await buildHeaders(req, requestId, accept);

  let response: TransportResponse;
  try {
    const transport = await resolveTransport();
    response = await transport({
      method: req.method,
      url: buildUrl(req.path, req.query),
      headers,
      body: req.body === undefined ? undefined : JSON.stringify(req.body),
      signal: options.signal,
      ...(options.stream ? { stream: true } : {}),
    });
  } catch (error) {
    normalizeFailure(error, req, options, requestId);
  }

  const serverRequestId = response.headers.get("x-request-id") ?? requestId;
  if (response.status < 200 || response.status >= 300) {
    throw toApiError(response.status, response.body, serverRequestId);
  }
  return response;
}

function send(req: ApiRequest, requestId: string): Promise<TransportResponse> {
  const timeoutMs = req.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const signal = req.signal ? AbortSignal.any([req.signal, timeoutSignal]) : timeoutSignal;
  return sendWith(req, requestId, { signal, timeoutSignal, timeoutMs });
}

async function execute(req: ApiRequest): Promise<{ response: TransportResponse; requestId: string }> {
  const retries = req.retries ?? (req.method === "GET" ? 2 : 0);
  const requestId = crypto.randomUUID();
  for (let attempt = 0; ; attempt += 1) {
    try {
      const response = await send(req, requestId);
      return { response, requestId: response.headers.get("x-request-id") ?? requestId };
    } catch (error) {
      if (!(error instanceof ApiError) || !error.retryable || attempt >= retries) throw error;
      logger.debug("Retrying request", { method: req.method, path: req.path, attempt, code: error.code });
      await wait(RETRY_BASE_DELAY_MS * 2 ** attempt, req.signal);
    }
  }
}

/** Send a request and validate the response body against `schema`. */
export async function request<T>(schema: z.ZodType<T>, req: ApiRequest): Promise<T> {
  const { response, requestId } = await execute(req);
  const parsed = schema.safeParse(response.body);
  if (!parsed.success) {
    logger.error("Response did not match schema", {
      method: req.method,
      path: req.path,
      requestId,
      issueCount: parsed.error.issues.length,
    });
    throw new ApiError({
      status: response.status,
      code: "invalid_response",
      message: "The server response did not match the expected schema.",
      requestId,
      details: parsed.error.issues,
    });
  }
  return parsed.data;
}

/** Send a request whose response has no body worth reading (e.g. 204). */
export async function requestVoid(req: ApiRequest): Promise<void> {
  await execute(req);
}

// --- Streaming (NDJSON) -----------------------------------------------------

/** Resettable timer: for streams the timeout bounds the silence between chunks. */
class IdleTimeout {
  private readonly controller = new AbortController();
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly ms: number) {
    this.reset();
  }

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  reset(): void {
    clearTimeout(this.timer);
    this.timer = setTimeout(
      () => this.controller.abort(new DOMException("The stream went idle.", "TimeoutError")),
      this.ms,
    );
  }

  clear(): void {
    clearTimeout(this.timer);
  }
}

export interface NdjsonOptions {
  /** Used in normalized errors. */
  requestId: string;
  status?: number;
  signal?: AbortSignal;
  /** Called for every received chunk (idle-timeout bookkeeping). */
  onChunk?: () => void;
}

function parseNdjsonLine<T>(
  schema: z.ZodType<T>,
  raw: string,
  lineNumber: number,
  options: NdjsonOptions,
): T {
  const invalid = (message: string, details?: unknown) =>
    new ApiError({
      status: options.status ?? 200,
      code: "invalid_response",
      message,
      requestId: options.requestId,
      details,
    });
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    throw invalid(`The server sent a malformed stream event (line ${lineNumber}).`);
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    logger.error("Stream event did not match schema", {
      requestId: options.requestId,
      line: lineNumber,
      issueCount: parsed.error.issues.length,
    });
    throw invalid(
      `A stream event did not match the expected schema (line ${lineNumber}).`,
      parsed.error.issues,
    );
  }
  return parsed.data;
}

/**
 * Parse a newline-delimited JSON byte stream, validating every line with `schema`.
 * Lines may span chunk boundaries; blank lines are ignored; a malformed or invalid line
 * throws an ApiError "invalid_response". Aborting `signal` (or returning early from the
 * iteration) cancels the underlying stream.
 */
export async function* parseNdjson<T>(
  stream: ReadableStream<Uint8Array>,
  schema: z.ZodType<T>,
  options: NdjsonOptions,
): AsyncGenerator<T, void, undefined> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const { signal } = options;
  const onAbort = () => {
    reader.cancel(signal?.reason).catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  let buffer = "";
  let lineNumber = 0;
  try {
    for (;;) {
      if (signal?.aborted) throw signal.reason;
      const { done, value } = await reader.read();
      if (signal?.aborted) throw signal.reason;
      if (value) {
        options.onChunk?.();
        buffer += decoder.decode(value, { stream: true });
      }
      if (done) buffer += decoder.decode();
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const raw = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        lineNumber += 1;
        if (raw.trim()) yield parseNdjsonLine(schema, raw, lineNumber, options);
        newline = buffer.indexOf("\n");
      }
      if (done) {
        if (buffer.trim()) yield parseNdjsonLine(schema, buffer, lineNumber + 1, options);
        return;
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    // Stopped early or failed: release the connection. A no-op once the stream is done.
    reader.cancel().catch(() => undefined);
  }
}

/**
 * Send a request to a streaming (NDJSON) endpoint and yield each validated event.
 * Same headers, request id, auth and error normalization as `request`; `timeoutMs`
 * bounds the wait for the response and for each subsequent chunk. Never retried.
 */
export async function* requestStream<T>(
  schema: z.ZodType<T>,
  req: ApiRequest,
  /** Called once the stream is open, with the request id to quote in errors. */
  onOpen?: (requestId: string) => void,
): AsyncGenerator<T, void, undefined> {
  const requestId = crypto.randomUUID();
  const timeoutMs = req.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const idle = new IdleTimeout(timeoutMs);
  const signal = req.signal ? AbortSignal.any([req.signal, idle.signal]) : idle.signal;
  const options: SendOptions = { signal, timeoutSignal: idle.signal, timeoutMs, stream: true };
  try {
    const response = await sendWith(req, requestId, options);
    const streamRequestId = response.headers.get("x-request-id") ?? requestId;
    if (!response.stream) {
      throw new ApiError({
        status: response.status,
        code: "invalid_response",
        message: "The server did not return a stream.",
        requestId: streamRequestId,
      });
    }
    idle.reset();
    onOpen?.(streamRequestId);
    try {
      yield* parseNdjson(response.stream, schema, {
        requestId: streamRequestId,
        status: response.status,
        signal,
        onChunk: () => idle.reset(),
      });
    } catch (error) {
      normalizeFailure(error, req, options, streamRequestId);
    }
  } finally {
    idle.clear();
  }
}

/** Run `promise`, resolving to null when it fails with a 404 carrying `code`. */
export async function nullOnNotFound<T>(promise: Promise<T>, code: string): Promise<T | null> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.code === code) return null;
    throw error;
  }
}

// --- UI error info ----------------------------------------------------------

export interface ErrorInfo {
  title: string;
  message: string;
  requestId?: string;
  code: string;
}

function titleFor(error: ApiError): string {
  switch (error.code) {
    case "network_error":
      return "Can't reach the server";
    case "timeout":
      return "The request timed out";
    case "invalid_response":
      return "Unexpected server response";
    case "version_conflict":
      return "Architecture updated";
  }
  if (error.status === 401) return "Sign-in required";
  if (error.status === 403) return "Not allowed";
  if (error.status === 404) return "Not found";
  if (error.status === 409) return "Conflict";
  if (error.status === 422) return "Request was rejected";
  if (error.status === 429) return "Too many requests";
  if (error.status >= 500) return "Server error";
  return "Request failed";
}

/** Normalize any thrown value into copy for an actionable error state (spec §49). */
export function getErrorInfo(error: unknown): ErrorInfo {
  if (error instanceof ApiError) {
    return { title: titleFor(error), message: error.message, requestId: error.requestId, code: error.code };
  }
  if (error instanceof Error) {
    return { title: "Unexpected error", message: error.message, code: "unknown" };
  }
  return { title: "Unexpected error", message: "An unknown error occurred.", code: "unknown" };
}

/**
 * Structured, minimal frontend logging (spec §86).
 *
 * Never log a full Architecture IR, credentials, prompts or sensitive infrastructure
 * data. Pass identifiers (projectId, version, requestId, code) in `context` instead.
 */

export type LogLevel = "debug" | "info" | "warn" | "error";
export type LogContext = Record<string, unknown>;

const debugEnabled = process.env.NODE_ENV === "development";

function emit(level: LogLevel, message: string, context?: LogContext): void {
  if (level === "debug" && !debugEnabled) return;
  const entry = { level, message, time: new Date().toISOString(), ...context };
  console[level](`[architectos] ${message}`, entry);
}

export const logger = {
  debug: (message: string, context?: LogContext) => emit("debug", message, context),
  info: (message: string, context?: LogContext) => emit("info", message, context),
  warn: (message: string, context?: LogContext) => emit("warn", message, context),
  error: (message: string, context?: LogContext) => emit("error", message, context),
};

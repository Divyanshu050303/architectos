import type { z } from "zod";

/** First message per field from a failed safeParse, keyed by the top-level field name. */
export function fieldErrors<K extends string>(
  error: z.ZodError,
  keys: readonly K[],
): Partial<Record<K, string>> {
  const errors: Partial<Record<K, string>> = {};
  for (const issue of error.issues) {
    const key = issue.path[0];
    if (typeof key === "string" && (keys as readonly string[]).includes(key) && !errors[key as K]) {
      errors[key as K] = issue.message;
    }
  }
  return errors;
}

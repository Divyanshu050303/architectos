/**
 * Public, build-time configuration (spec §84). Only NEXT_PUBLIC_* values belong here;
 * they are inlined into the browser bundle, so they must never be secrets.
 */
import { z } from "zod";

const PublicEnvSchema = z.object({
  NEXT_PUBLIC_API_URL: z.url().default("http://localhost:8000/api/v1"),
  NEXT_PUBLIC_APP_ENV: z.enum(["development", "test", "staging", "production"]).default("development"),
  NEXT_PUBLIC_API_MOCKS: z.enum(["true", "false"]).default("false"),
});

// Each variable is referenced explicitly: Next only inlines direct `process.env.NAME` reads.
const parsed = PublicEnvSchema.parse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || undefined,
  NEXT_PUBLIC_APP_ENV: process.env.NEXT_PUBLIC_APP_ENV || undefined,
  NEXT_PUBLIC_API_MOCKS: process.env.NEXT_PUBLIC_API_MOCKS || undefined,
});

export const config = {
  apiUrl: parsed.NEXT_PUBLIC_API_URL.replace(/\/$/, ""),
  appEnv: parsed.NEXT_PUBLIC_APP_ENV,
  useMocks: parsed.NEXT_PUBLIC_API_MOCKS === "true",
} as const;

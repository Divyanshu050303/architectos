/**
 * Conversion between the saved Requirements contract and the string-valued form
 * (schemas/requirements.ts RequirementsFormSchema does validation and parsing).
 */
import type { z } from "zod";

import type { RequirementsInput } from "@/api/requirements";
import type { RequirementsFormInput, RequirementsFormSchema } from "@/schemas/requirements";
import type { Requirements } from "@/types/project";

export type RequirementsFormValues = { [K in keyof RequirementsFormInput]: string };
export type RequirementsFormField = keyof RequirementsFormValues;
export type RequirementsFormErrors = Partial<Record<RequirementsFormField, string>>;

function numberText(value: number | null): string {
  return value === null ? "" : String(value);
}

export function toFormValues(requirements: Requirements): RequirementsFormValues {
  const nf = requirements.nonFunctional;
  return {
    description: requirements.description,
    functional: requirements.functional.join("\n"),
    dailyActiveUsers: numberText(nf.dailyActiveUsers),
    peakRps: numberText(nf.peakRps),
    // Stored as a ratio (0.999); edited as a percentage (99.9). toPrecision trims float noise.
    availabilityTarget:
      nf.availabilityTarget === null ? "" : String(Number((nf.availabilityTarget * 100).toPrecision(10))),
    p99LatencyMs: numberText(nf.p99LatencyMs),
    dataRetentionDays: numberText(nf.dataRetentionDays),
    regions: nf.regions.join(", "),
  };
}

function splitList(text: string, separator: RegExp): string[] {
  return text
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function toRequirementsInput(parsed: z.output<typeof RequirementsFormSchema>): RequirementsInput {
  return {
    description: parsed.description,
    functional: splitList(parsed.functional, /\r?\n/),
    nonFunctional: {
      dailyActiveUsers: parsed.dailyActiveUsers,
      peakRps: parsed.peakRps,
      availabilityTarget: parsed.availabilityTarget,
      p99LatencyMs: parsed.p99LatencyMs,
      dataRetentionDays: parsed.dataRetentionDays,
      regions: splitList(parsed.regions, /,/),
    },
  };
}

/** The API requires whole numbers for these; the shared form schema only checks positivity. */
export function wholeNumberErrors(parsed: z.output<typeof RequirementsFormSchema>): RequirementsFormErrors {
  const errors: RequirementsFormErrors = {};
  if (parsed.dailyActiveUsers !== null && !Number.isInteger(parsed.dailyActiveUsers)) {
    errors.dailyActiveUsers = "Daily active users must be a whole number";
  }
  if (parsed.dataRetentionDays !== null && !Number.isInteger(parsed.dataRetentionDays)) {
    errors.dataRetentionDays = "Retention must be a whole number of days";
  }
  return errors;
}

export function sameValues(a: RequirementsFormValues, b: RequirementsFormValues): boolean {
  return (Object.keys(a) as RequirementsFormField[]).every((key) => a[key] === b[key]);
}

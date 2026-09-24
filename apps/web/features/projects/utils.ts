import type { MeterTone } from "@/components/ui/progress";

/** Display tone for a backend-computed utilisation ratio (0..1). Presentation only. */
export function utilizationTone(ratio: number): MeterTone {
  if (ratio >= 0.85) return "danger";
  if (ratio >= 0.7) return "warning";
  return "accent";
}

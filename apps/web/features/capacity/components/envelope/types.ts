import type { EnvelopePoint } from "@/types/capacity";
import type { EvolutionStage } from "@/types/evolution";

export interface OperatingEnvelopeChartProps {
  points: readonly EnvelopePoint[];
  maxSupportedDailyActiveUsers: number;
  /**
   * Evolution stages (V1, V2, V3 …). With at least two stages and one of them current, the chart
   * adds the version dimension (spec §36): one column per stage, DAU on a log y-axis.
   */
  stages?: readonly EvolutionStage[];
  className?: string;
}

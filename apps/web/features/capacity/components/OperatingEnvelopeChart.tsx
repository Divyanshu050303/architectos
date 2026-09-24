import { versionColumns } from "../utils/envelope";
import { LoadEnvelope } from "./envelope/LoadEnvelope";
import type { OperatingEnvelopeChartProps } from "./envelope/types";
import { VersionEnvelope } from "./envelope/VersionEnvelope";

export type { OperatingEnvelopeChartProps } from "./envelope/types";
export {
  describeEnvelope,
  describeVersionEnvelope,
  type EnvelopeColumn,
  versionColumns,
} from "../utils/envelope";

/**
 * The operating envelope (spec §36): load levels the backend evaluated, on a log-scale
 * DAU axis, with the supported region shaded up to the maximum supported load. Every
 * position comes from backend numbers; the chart only maps them to pixels.
 */
export function OperatingEnvelopeChart({ stages, ...props }: OperatingEnvelopeChartProps) {
  const columns = versionColumns(stages ?? [], props.points);
  if (columns && props.points.some((p) => p.dailyActiveUsers > 0)) {
    return <VersionEnvelope columns={columns} {...props} />;
  }
  return <LoadEnvelope {...props} />;
}

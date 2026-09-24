import { formatCompact, formatNumber } from "@/lib/formatting";
import { cn } from "@/lib/utils";

import { describeEnvelope, DRAW_ORDER, STATUS_TEXT } from "../../utils/envelope";
import { Legend, Marker } from "./EnvelopeMarker";
import type { OperatingEnvelopeChartProps } from "./types";

const WIDTH = 640;
const HEIGHT = 140;
const PAD_X = 40;
const TRACK_Y = 76;

/** The single-version view: load levels on a horizontal log-scale DAU track. */
export function LoadEnvelope({
  points,
  maxSupportedDailyActiveUsers,
  className,
}: Omit<OperatingEnvelopeChartProps, "stages">) {
  const plotted = points.filter((p) => p.dailyActiveUsers > 0);
  if (plotted.length === 0) {
    return <p className="text-sm text-muted">The capacity engine returned no envelope points.</p>;
  }

  const values = plotted.map((p) => p.dailyActiveUsers);
  if (maxSupportedDailyActiveUsers > 0) values.push(maxSupportedDailyActiveUsers);
  const logLo = Math.log10(Math.min(...values) / 2);
  const logHi = Math.log10(Math.max(...values) * 1.6);
  const x = (value: number) => PAD_X + ((Math.log10(value) - logLo) / (logHi - logLo)) * (WIDTH - 2 * PAD_X);

  const maxX = maxSupportedDailyActiveUsers > 0 ? x(maxSupportedDailyActiveUsers) : WIDTH - PAD_X;
  const maxAnchor = maxX > WIDTH - 110 ? "end" : maxX < 110 ? "start" : "middle";
  const ordered = [...plotted].sort((a, b) => DRAW_ORDER[a.status] - DRAW_ORDER[b.status]);
  const summary = describeEnvelope(points, maxSupportedDailyActiveUsers);

  return (
    <figure className={cn("flex flex-col gap-3", className)}>
      <svg
        role="img"
        aria-label={summary}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full"
        fontSize={11}
      >
        {/* Supported region and the region beyond the envelope */}
        <rect
          x={PAD_X}
          y={TRACK_Y - 14}
          width={Math.max(maxX - PAD_X, 0)}
          height={28}
          rx={4}
          fill="var(--accent-soft)"
        />
        <rect
          x={maxX}
          y={TRACK_Y - 14}
          width={Math.max(WIDTH - PAD_X - maxX, 0)}
          height={28}
          rx={4}
          fill="var(--danger-soft)"
        />
        <line x1={PAD_X} y1={TRACK_Y} x2={WIDTH - PAD_X} y2={TRACK_Y} stroke="var(--border-strong)" />

        {/* Maximum supported load */}
        <line
          x1={maxX}
          y1={22}
          x2={maxX}
          y2={TRACK_Y + 18}
          stroke="var(--border-control)"
          strokeDasharray="3 3"
        />
        <text x={maxX} y={14} textAnchor={maxAnchor} fill="var(--text-secondary)" fontWeight={600}>
          Max supported ~{formatCompact(maxSupportedDailyActiveUsers)}
        </text>

        {ordered.map((point) => {
          const px = x(point.dailyActiveUsers);
          return (
            <g key={`${point.label}-${point.dailyActiveUsers}`}>
              {point.status !== "supported" ? (
                <text
                  x={px}
                  y={TRACK_Y - 20}
                  textAnchor="middle"
                  fontWeight={600}
                  fill={
                    point.status === "current"
                      ? "var(--accent-fg)"
                      : point.status === "warning"
                        ? "var(--warning-fg)"
                        : "var(--danger-fg)"
                  }
                >
                  {point.status === "current" ? "Current" : STATUS_TEXT[point.status]}
                </text>
              ) : null}
              <Marker status={point.status} x={px} y={TRACK_Y} />
              <text
                x={px}
                y={TRACK_Y + 30}
                textAnchor="middle"
                fill="var(--text-primary)"
                className="tabular"
              >
                {point.label}
              </text>
            </g>
          );
        })}

        <text x={PAD_X} y={HEIGHT - 6} fill="var(--accent-fg)">
          Supported range
        </text>
        <text x={WIDTH - PAD_X} y={HEIGHT - 6} textAnchor="end" fill="var(--danger-fg)">
          Beyond envelope
        </text>
      </svg>

      <Legend axis="Daily active users (log scale)" />

      <table className="sr-only">
        <caption>Operating envelope data points</caption>
        <thead>
          <tr>
            <th scope="col">Load level</th>
            <th scope="col">Daily active users</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={`${point.label}-${point.dailyActiveUsers}`}>
              <th scope="row">{point.label}</th>
              <td>{formatNumber(point.dailyActiveUsers, 0)}</td>
              <td>{STATUS_TEXT[point.status]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

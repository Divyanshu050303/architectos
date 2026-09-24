import { formatCompact, formatNumber } from "@/lib/formatting";
import { cn } from "@/lib/utils";

import {
  describeVersionEnvelope,
  DRAW_ORDER,
  type EnvelopeColumn,
  markerOffsets,
  spacedLabels,
  STAGE_TEXT,
  STATUS_TEXT,
} from "../../utils/envelope";
import { Legend, Marker } from "./EnvelopeMarker";
import type { OperatingEnvelopeChartProps } from "./types";

const V_WIDTH = 640;
const V_HEIGHT = 300;
const V_LEFT = 52;
const V_RIGHT = 12;
const V_TOP = 28;
const V_BOTTOM = 48;

/** The version dimension (spec §36): one column per evolution stage, DAU on a log y-axis. */
export function VersionEnvelope({
  columns,
  className,
}: Omit<OperatingEnvelopeChartProps, "stages"> & { columns: EnvelopeColumn[] }) {
  const values = [
    ...(columns[0]?.points.map((p) => p.dailyActiveUsers) ?? []),
    ...columns.map((c) => c.stage.maxSupportedDailyActiveUsers).filter((v) => v > 0),
  ];
  const logLo = Math.floor(Math.log10(Math.min(...values) / 1.5));
  const logHi = Math.log10(Math.max(...values) * 1.6);
  const plotH = V_HEIGHT - V_TOP - V_BOTTOM;
  const y = (value: number) => V_TOP + (1 - (Math.log10(value) - logLo) / (logHi - logLo)) * plotH;
  const colW = (V_WIDTH - V_LEFT - V_RIGHT) / columns.length;
  const bottom = V_HEIGHT - V_BOTTOM;

  const ticks: number[] = [];
  for (let k = Math.ceil(logLo); k <= Math.floor(logHi); k++) ticks.push(10 ** k);

  // Nudge markers sideways when two load levels sit closer than a marker's height.
  const offsets = markerOffsets(columns[0]?.points ?? [], y);

  const summary = describeVersionEnvelope(columns);

  return (
    <figure className={cn("flex flex-col gap-3", className)}>
      <svg
        role="img"
        aria-label={summary}
        viewBox={`0 0 ${V_WIDTH} ${V_HEIGHT}`}
        className="h-auto w-full"
        fontSize={11}
      >
        {/* Log-scale y axis */}
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={V_LEFT} x2={V_WIDTH - V_RIGHT} y1={y(tick)} y2={y(tick)} stroke="var(--border)" />
            <text
              x={V_LEFT - 8}
              y={y(tick) + 4}
              textAnchor="end"
              fill="var(--text-secondary)"
              className="tabular"
            >
              {formatCompact(tick)}
            </text>
          </g>
        ))}
        <line x1={V_LEFT} x2={V_LEFT} y1={V_TOP} y2={bottom} stroke="var(--border-strong)" />
        <line x1={V_LEFT} x2={V_WIDTH - V_RIGHT} y1={bottom} y2={bottom} stroke="var(--border-strong)" />
        <text x={V_LEFT - 8} y={V_TOP - 12} textAnchor="end" fill="var(--text-secondary)" fontWeight={600}>
          DAU
        </text>

        {columns.map((column, index) => {
          const left = V_LEFT + index * colW;
          const cx = left + colW / 2;
          const max = column.stage.maxSupportedDailyActiveUsers;
          const maxY = max > 0 ? Math.min(Math.max(y(max), V_TOP), bottom) : bottom;
          const ordered = [...column.points].sort((a, b) => DRAW_ORDER[a.status] - DRAW_ORDER[b.status]);
          const labelY = column.current ? spacedLabels(column.points, y) : new Map<string, number>();
          const bandX = left + colW * 0.2;
          const bandW = colW * 0.6;
          return (
            <g key={column.stage.id}>
              {/* Supported region up to this stage's maximum; beyond it above. */}
              <rect x={bandX} y={maxY} width={bandW} height={bottom - maxY} fill="var(--accent-soft)" />
              <rect x={bandX} y={V_TOP} width={bandW} height={maxY - V_TOP} fill="var(--danger-soft)" />
              <line
                x1={bandX}
                x2={bandX + bandW}
                y1={maxY}
                y2={maxY}
                stroke="var(--border-control)"
                strokeDasharray="3 3"
              />
              <text x={bandX + 4} y={maxY - 4} fill="var(--text-secondary)">
                max ~{formatCompact(max)}
              </text>

              {ordered.map((point) => {
                const key = `${point.label}-${point.dailyActiveUsers}`;
                // Right of centre, clear of the "max" label at the band's left edge.
                const px = cx + 10 + (offsets.get(key) ?? 0);
                const py = y(point.dailyActiveUsers);
                return (
                  <g key={key}>
                    <title>{`${column.stage.label} · ${point.label} DAU: ${STATUS_TEXT[point.status]}`}</title>
                    <Marker status={point.status} x={px} y={py} />
                    {labelY.has(key) ? (
                      <text
                        x={cx + 44}
                        y={(labelY.get(key) ?? py) + 4}
                        fontWeight={600}
                        className="tabular"
                        fill={
                          point.status === "current"
                            ? "var(--accent-fg)"
                            : point.status === "warning"
                              ? "var(--warning-fg)"
                              : "var(--danger-fg)"
                        }
                      >
                        {point.status === "current" ? `Current ${point.label}` : point.label}
                      </text>
                    ) : null}
                  </g>
                );
              })}

              <text
                x={cx}
                y={bottom + 18}
                textAnchor="middle"
                fill="var(--text-primary)"
                fontWeight={column.current ? 700 : 500}
              >
                {column.stage.label}
              </text>
              <text x={cx} y={bottom + 34} textAnchor="middle" fill="var(--text-secondary)">
                {column.current ? "Current" : column.stage.status === "past" ? "Past" : "Planned"}
              </text>
            </g>
          );
        })}
      </svg>

      <Legend axis="Daily active users (log scale) by architecture version" />

      <table className="sr-only">
        <caption>Operating envelope by architecture version</caption>
        <thead>
          <tr>
            <th scope="col">Load level</th>
            {columns.map((column) => (
              <th key={column.stage.id} scope="col">
                {column.stage.label} ({STAGE_TEXT[column.stage.status]})
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Maximum supported DAU</th>
            {columns.map((column) => (
              <td key={column.stage.id}>{formatNumber(column.stage.maxSupportedDailyActiveUsers, 0)}</td>
            ))}
          </tr>
          {(columns[0]?.points ?? []).map((point, row) => (
            <tr key={`${point.label}-${point.dailyActiveUsers}`}>
              <th scope="row">
                {point.label} ({formatNumber(point.dailyActiveUsers, 0)} DAU)
              </th>
              {columns.map((column) => {
                const cell = column.points[row];
                return <td key={column.stage.id}>{cell ? STATUS_TEXT[cell.status] : "—"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

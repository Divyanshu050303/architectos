import { type PointStatus, STATUS_TEXT } from "../../utils/envelope";

/** Semantic marker glyphs, shared by the chart and its legend. All colours are theme tokens. */
export function Marker({ status, x, y }: { status: PointStatus; x: number; y: number }) {
  switch (status) {
    case "current":
      return (
        <circle cx={x} cy={y} r={7} fill="var(--accent-strong)" stroke="var(--surface)" strokeWidth={2} />
      );
    case "warning":
      return (
        <path
          d={`M ${x} ${y - 8} L ${x + 8} ${y + 6} L ${x - 8} ${y + 6} Z`}
          fill="var(--warning)"
          stroke="var(--surface)"
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
      );
    case "exceeded":
      return (
        <g stroke="var(--danger)" strokeWidth={2.5} strokeLinecap="round">
          <line x1={x - 6} y1={y - 6} x2={x + 6} y2={y + 6} />
          <line x1={x - 6} y1={y + 6} x2={x + 6} y2={y - 6} />
        </g>
      );
    case "supported":
      return (
        <g>
          <circle
            cx={x}
            cy={y}
            r={7}
            fill="var(--surface)"
            stroke="var(--border-control)"
            strokeWidth={1.5}
          />
          <path
            d={`M ${x - 3.5} ${y} l 2.5 2.5 l 4.5 -5`}
            fill="none"
            stroke="var(--text-secondary)"
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </g>
      );
  }
}

export function Legend({ axis }: { axis: string }) {
  return (
    <figcaption className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-fg-secondary">
      <span>{axis}</span>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1" aria-label="Legend">
        {(["current", "warning", "exceeded", "supported"] as const).map((status) => (
          <li key={status} className="flex items-center gap-1.5">
            <svg aria-hidden viewBox="0 0 20 20" className="size-4">
              <Marker status={status} x={10} y={10} />
            </svg>
            {STATUS_TEXT[status]}
          </li>
        ))}
      </ul>
    </figcaption>
  );
}

import type { Decision } from "@/types/architecture";
import { cn } from "@/lib/utils";

import { DecisionStatusBadge, formatAdrDate, formatAdrNumber } from "../format";

export interface DecisionListProps {
  decisions: readonly Decision[];
  selectedId: string | null;
  onSelect: (decisionId: string) => void;
}

/** ADRs in number order. */
export function DecisionList({ decisions, selectedId, onSelect }: DecisionListProps) {
  const sorted = [...decisions].sort((a, b) => a.number - b.number);
  return (
    <nav aria-label="Architecture decision records">
      <ul className="flex flex-col gap-1">
        {sorted.map((decision) => {
          const selected = decision.id === selectedId;
          return (
            <li key={decision.id}>
              <button
                type="button"
                aria-current={selected ? "true" : undefined}
                onClick={() => onSelect(decision.id)}
                className={cn(
                  "flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left transition-colors",
                  selected
                    ? "border-accent/50 bg-accent-soft"
                    : "border-transparent hover:border-default hover:bg-surface-2",
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="tabular text-xs text-fg-secondary">
                    {formatAdrNumber(decision.number)}
                  </span>
                  <DecisionStatusBadge status={decision.status} />
                </span>
                <span className="text-sm font-medium text-fg">{decision.title}</span>
                <time
                  dateTime={decision.date}
                  className={cn("text-xs", selected ? "text-fg-secondary" : "text-muted")}
                >
                  {formatAdrDate(decision.date)}
                </time>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

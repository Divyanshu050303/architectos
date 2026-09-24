import { Archive, CircleCheck, CircleDashed, CircleX, type LucideIcon } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import type { Decision } from "@/types/architecture";

/** 1 → "ADR-001". */
export function formatAdrNumber(number: number): string {
  return `ADR-${String(number).padStart(3, "0")}`;
}

const dateFormatter = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" });

/** ADR dates are calendar dates ("2026-09-02"); format them without a time-zone shift. */
export function formatAdrDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dateFormatter.format(date);
}

const STATUS_META: Record<Decision["status"], { label: string; tone: BadgeTone; Icon: LucideIcon }> = {
  proposed: { label: "Proposed", tone: "info", Icon: CircleDashed },
  accepted: { label: "Accepted", tone: "accent", Icon: CircleCheck },
  superseded: { label: "Superseded", tone: "neutral", Icon: Archive },
  rejected: { label: "Rejected", tone: "danger", Icon: CircleX },
};

export function DecisionStatusBadge({ status }: { status: Decision["status"] }) {
  const meta = STATUS_META[status];
  return (
    <Badge tone={meta.tone}>
      <meta.Icon aria-hidden />
      {meta.label}
    </Badge>
  );
}

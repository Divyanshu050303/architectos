import { AlertTriangle, OctagonAlert, ShieldCheck } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import type { RiskLevel } from "@/types/evolution";

const RISK_META: Record<RiskLevel, { label: string; tone: BadgeTone; Icon: typeof ShieldCheck }> = {
  low: { label: "Low risk", tone: "neutral", Icon: ShieldCheck },
  medium: { label: "Medium risk", tone: "warning", Icon: AlertTriangle },
  high: { label: "High risk", tone: "danger", Icon: OctagonAlert },
};

/** Backend-assessed risk: icon + text + colour, never colour alone (spec §62). */
export function RiskBadge({ risk, className }: { risk: RiskLevel; className?: string }) {
  const meta = RISK_META[risk];
  return (
    <Badge tone={meta.tone} className={className}>
      <meta.Icon aria-hidden />
      {meta.label}
    </Badge>
  );
}

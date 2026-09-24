import type { Finding, Severity } from "@/types/validation";
import { AppearListItem } from "@/components/feedback/AppearListItem";
import { cn } from "@/lib/utils";

import { pluralFindings, SEVERITY_META } from "../severity";
import { FindingCard } from "./FindingCard";

export interface SeverityGroupProps {
  projectId: string;
  severity: Severity;
  findings: readonly Finding[];
}

export function SeverityGroup({ projectId, severity, findings }: SeverityGroupProps) {
  const meta = SEVERITY_META[severity];
  const headingId = `severity-${severity}`;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h2 id={headingId} className="flex items-center gap-2">
        <meta.Icon aria-hidden className={cn("size-4", meta.iconClass)} />
        <span className="text-2xs font-semibold tracking-[0.06em] text-fg uppercase">{meta.label}</span>
        <span className="text-xs text-muted">{pluralFindings(findings.length)}</span>
      </h2>
      <ul className="flex flex-col gap-3">
        {findings.map((finding, index) => (
          <AppearListItem key={finding.id} index={index}>
            <FindingCard projectId={projectId} finding={finding} />
          </AppearListItem>
        ))}
      </ul>
    </section>
  );
}

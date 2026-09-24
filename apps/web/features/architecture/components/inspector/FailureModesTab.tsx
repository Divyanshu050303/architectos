import Link from "next/link";

import { AppearListItem } from "@/components/feedback/AppearListItem";
import { Badge } from "@/components/ui/badge";

import { SEVERITY_META, WhyButton } from "./shared";
import type { NodeInspectorProps } from "./types";

export function FailureModesTab({
  projectId,
  findings,
  onOpenEvidence,
}: Pick<NodeInspectorProps, "projectId" | "findings" | "onOpenEvidence">) {
  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col gap-2">
        {findings.map((finding, index) => {
          const severity = SEVERITY_META[finding.severity];
          return (
            <AppearListItem
              key={finding.id}
              index={index}
              className="flex flex-col gap-1.5 rounded-md border border-default p-2.5"
            >
              <div className="flex items-center gap-2">
                <Badge tone={severity.tone}>{severity.label}</Badge>
                <span className="label-caps">{finding.category}</span>
              </div>
              <p className="text-sm font-medium text-fg">{finding.title}</p>
              <p className="text-xs text-fg-secondary">{finding.whyItMatters}</p>
              <p className="text-xs text-fg">
                <span className="text-muted">Recommendation: </span>
                {finding.recommendation}
              </p>
              {finding.evidenceIds[0] ? (
                <div>
                  <WhyButton
                    evidenceId={finding.evidenceIds[0]}
                    label={finding.title}
                    onOpen={onOpenEvidence}
                  />
                </div>
              ) : null}
            </AppearListItem>
          );
        })}
      </ul>
      <Link
        href={`/project/${projectId}/validation`}
        className="text-xs font-medium text-fg-secondary underline-offset-2 hover:text-fg hover:underline"
      >
        View all findings in Validation
      </Link>
    </div>
  );
}

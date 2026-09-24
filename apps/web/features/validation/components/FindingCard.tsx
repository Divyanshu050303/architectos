"use client";

import { CircleHelp, EyeOff, FilePlus2, LocateFixed, MapPin, Undo2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { CreateDecisionDialog } from "@/features/decisions/components/CreateDecisionDialog";
import { useFixFinding } from "@/hooks/use-proposals";
import { useSetFindingStatus } from "@/hooks/use-validation";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/stores/ui-store";
import type { DecisionInput } from "@/types/architecture";
import type { Finding } from "@/types/validation";

import { toastError } from "../notify";
import { CATEGORY_LABEL, SEVERITY_META, SeverityBadge } from "../severity";

export interface FindingCardProps {
  projectId: string;
  finding: Finding;
}

function adrFromFinding(finding: Finding): Partial<DecisionInput> {
  return {
    title: finding.title,
    context: `${finding.whyItMatters}\n\nValidation finding "${finding.title}" at ${finding.location} (rule ${finding.ruleId}).`,
    decision: finding.recommendation,
    consequences: "",
    relatedNodeIds: finding.nodeIds,
    sourceFindingId: finding.id,
  };
}

/** A button that stays focusable and explains itself when unavailable. */
function DisabledWithReason({ reason, children }: { reason: string; children: React.ReactElement }) {
  return (
    <Tooltip content={reason}>
      <span tabIndex={0} className="inline-flex rounded-sm">
        {children}
      </span>
    </Tooltip>
  );
}

/** One validation finding with Explain / Locate / Fix / Ignore / Create ADR (spec §38–39). */
export function FindingCard({ projectId, finding }: FindingCardProps) {
  const router = useRouter();
  const titleId = useId();
  const openEvidence = useUiStore((s) => s.openEvidence);
  const fix = useFixFinding(projectId);
  const setStatus = useSetFindingStatus(projectId);
  const [adrOpen, setAdrOpen] = useState(false);

  const meta = SEVERITY_META[finding.severity];
  const ignored = finding.status === "ignored";
  const evidenceId = finding.evidenceIds[0] ?? null;
  const firstNode = finding.nodeIds[0];
  const base = `/project/${encodeURIComponent(projectId)}`;

  const locate = () => {
    if (!firstNode) return;
    const highlight = finding.nodeIds.map(encodeURIComponent).join(",");
    router.push(`${base}/architecture?highlight=${highlight}&node=${encodeURIComponent(firstNode)}`);
  };

  const onFix = async () => {
    try {
      const proposal = await fix.mutateAsync(finding.id);
      router.push(`${base}/architecture?proposal=${encodeURIComponent(proposal.id)}`);
    } catch (error) {
      toastError("Could not create a fix. No changes were applied.", error);
    }
  };

  const toggleIgnored = () => {
    const status = ignored ? "open" : "ignored";
    setStatus.mutate(
      { findingId: finding.id, status },
      {
        onSuccess: () =>
          toast(status === "ignored" ? "Finding ignored" : "Finding restored", {
            description: finding.title,
          }),
        onError: (error) =>
          toastError(
            status === "ignored" ? "Could not ignore the finding." : "Could not restore the finding.",
            error,
          ),
      },
    );
  };

  const explainButton = (
    <Button
      size="sm"
      variant="ghost"
      disabled={!evidenceId}
      onClick={() => evidenceId && openEvidence(evidenceId)}
    >
      <CircleHelp aria-hidden className="size-3.5" />
      Explain
    </Button>
  );
  const locateButton = (
    <Button size="sm" variant="ghost" disabled={!firstNode} onClick={locate}>
      <LocateFixed aria-hidden className="size-3.5" />
      Locate
    </Button>
  );

  return (
    <article
      aria-labelledby={titleId}
      data-status={finding.status}
      className={cn(
        "flex flex-col gap-3 rounded-md border border-default bg-surface px-4 py-3",
        ignored && "border-dashed bg-surface-2/60",
      )}
    >
      <header className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={finding.severity} />
          <Badge>{CATEGORY_LABEL[finding.category]}</Badge>
          {ignored ? (
            <Badge>
              <EyeOff aria-hidden />
              Ignored
            </Badge>
          ) : null}
          <span className="tabular ml-auto text-2xs text-muted">{finding.ruleId}</span>
        </div>
        <h3
          id={titleId}
          className={cn(
            "flex items-start gap-2 text-sm font-semibold",
            ignored ? "text-fg-secondary" : "text-fg",
          )}
        >
          <meta.Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", meta.iconClass)} />
          {finding.title}
        </h3>
        <p className="flex items-center gap-1.5 text-xs text-fg-secondary">
          <MapPin aria-hidden className="size-3.5 shrink-0 text-muted" />
          <span className="sr-only">Location: </span>
          <span className="tabular">{finding.location}</span>
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <section className="flex flex-col gap-1">
          <h4 className="label-caps">Why it matters</h4>
          <p className="text-sm text-fg-secondary">{finding.whyItMatters}</p>
        </section>
        <section className="flex flex-col gap-1">
          <h4 className="label-caps">Recommendation</h4>
          <p className="text-sm text-fg">{finding.recommendation}</p>
        </section>
      </div>

      <div
        role="group"
        aria-label={`Actions for ${finding.title}`}
        className="flex flex-wrap items-center gap-1 print:hidden"
      >
        {evidenceId ? (
          explainButton
        ) : (
          <DisabledWithReason reason="No evidence was recorded for this finding">
            {explainButton}
          </DisabledWithReason>
        )}
        {firstNode ? (
          locateButton
        ) : (
          <DisabledWithReason reason="This finding is not tied to a component">
            {locateButton}
          </DisabledWithReason>
        )}
        {finding.fixable && !ignored ? (
          <Button size="sm" variant="ai" onClick={() => void onFix()} loading={fix.isPending}>
            {fix.isPending ? null : <span aria-hidden>✦</span>}
            {fix.isPending ? "Preparing fix…" : "Fix"}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" onClick={() => setAdrOpen(true)}>
          <FilePlus2 aria-hidden className="size-3.5" />
          Create ADR
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={toggleIgnored}
          loading={setStatus.isPending}
          className="ml-auto"
        >
          {setStatus.isPending ? null : ignored ? (
            <Undo2 aria-hidden className="size-3.5" />
          ) : (
            <EyeOff aria-hidden className="size-3.5" />
          )}
          {ignored ? "Restore" : "Ignore"}
        </Button>
      </div>

      <CreateDecisionDialog
        projectId={projectId}
        open={adrOpen}
        onOpenChange={setAdrOpen}
        initial={adrFromFinding(finding)}
      />
    </article>
  );
}

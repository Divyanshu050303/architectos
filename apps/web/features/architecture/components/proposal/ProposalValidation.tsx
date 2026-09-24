"use client";

/**
 * Validation of a proposal, shown before it can be applied (spec §89: proposal →
 * validate → preview → approve → apply). Produced by the backend validator, so it is
 * presented as a deterministic validator result, not as AI text (§123–124). Severity is always
 * icon + text + colour, never colour alone.
 */
import { CircleCheck, TriangleAlert } from "lucide-react";
import { useId } from "react";

import type { ProposalFinding, ProposalValidation as Validation } from "@/api/proposals";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { pluralFindings, SEVERITY_META } from "@/features/validation/severity";
import { cn } from "@/lib/utils";
import type { Severity } from "@/types/validation";

const BLOCKING: ReadonlySet<Severity> = new Set(["critical", "high"]);

/** New critical/high findings: applying them needs an explicit confirmation. */
export function blockingFindings(validation: Validation | null): ProposalFinding[] {
  return validation?.newFindings.filter((f) => BLOCKING.has(f.severity)) ?? [];
}

function FindingLine({ finding }: { finding: ProposalFinding }) {
  const meta = SEVERITY_META[finding.severity];
  return (
    <li className="flex items-baseline gap-2 text-sm">
      <meta.Icon aria-hidden className={cn("size-3.5 shrink-0 translate-y-0.5", meta.iconClass)} />
      <span className={cn("shrink-0 text-2xs font-medium", meta.iconClass)}>{meta.label}</span>
      <span className="min-w-0 text-fg">
        {finding.title} <span className="text-muted">· {finding.location}</span>
      </span>
    </li>
  );
}

export function ProposalValidationSection({ validation }: { validation: Validation | null }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <h3 id={headingId} className="label-caps">
          Validation
        </h3>
        <ProvenanceTag kind="calculated" label="Validator" />
      </div>
      {validation === null ? (
        <p className="text-xs text-warning-fg">
          This proposal was not validated. Review the changes carefully before applying.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5 rounded-md border border-default px-2.5 py-2">
          {validation.passes && validation.newFindings.length === 0 ? (
            <p className="flex items-center gap-1.5 text-sm font-medium text-accent-fg">
              <CircleCheck aria-hidden className="size-4" />
              Passes validation
            </p>
          ) : (
            <p className="flex items-center gap-1.5 text-sm font-medium text-warning-fg">
              <TriangleAlert aria-hidden className="size-4" />
              {validation.newFindings.length > 0
                ? `${validation.newFindings.length} new ${validation.newFindings.length === 1 ? "finding" : "findings"}`
                : "Does not pass validation"}
            </p>
          )}
          <p className="text-xs text-fg-secondary">{validation.summary}</p>
          {validation.newFindings.length > 0 ? (
            <ul aria-label="New findings" className="flex flex-col gap-1">
              {validation.newFindings.map((finding, i) => (
                <FindingLine key={`${finding.severity}:${finding.title}:${i}`} finding={finding} />
              ))}
            </ul>
          ) : null}
          {validation.resolvedFindingIds.length > 0 ? (
            <div className="flex flex-col gap-1">
              <p className="flex items-center gap-1.5 text-xs text-accent-fg">
                <CircleCheck aria-hidden className="size-3.5" />
                Resolves {pluralFindings(validation.resolvedFindingIds.length)}
              </p>
              <ul aria-label="Resolved findings" className="flex flex-wrap gap-1">
                {validation.resolvedFindingIds.map((id) => (
                  <li
                    key={id}
                    className="tabular rounded-sm border border-default bg-surface-2 px-1.5 text-2xs text-fg-secondary line-through decoration-muted"
                  >
                    {id}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

export interface ApplyConfirmDialogProps {
  open: boolean;
  findings: readonly ProposalFinding[];
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}

/** Confirmation before applying a change that introduces critical/high findings. */
export function ApplyConfirmDialog({ open, findings, onConfirm, onOpenChange }: ApplyConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Apply with new findings?"
        description={`Validation found ${findings.length} new critical or high ${
          findings.length === 1 ? "finding" : "findings"
        } in the proposed architecture.`}
      >
        <ul className="flex flex-col gap-1">
          {findings.map((finding, i) => (
            <FindingLine key={`${finding.severity}:${finding.title}:${i}`} finding={finding} />
          ))}
        </ul>
        <DialogFooter>
          <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
          >
            Apply anyway
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

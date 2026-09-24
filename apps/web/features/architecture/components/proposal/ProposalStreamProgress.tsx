"use client";

/**
 * Live progress of a streamed proposal (spec §87–88): the backend's real pipeline steps
 * and the AI's explanatory text as it arrives. Text is appended in batches, each its own
 * node, so the polite log region announces additions rather than every token. No
 * architecture change is shown here; the proposal arrives whole, afterwards.
 */
import type { ProposalStep } from "@/api/proposals";
import { type LoadingStep, LoadingSteps } from "@/components/feedback/LoadingSteps";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Button } from "@/components/ui/button";

/** Shown until the first progress event: honest, the request is in flight. */
const SENDING_STEP: LoadingStep = { id: "sending", label: "Sending request", status: "running" };

export interface ProposalStreamProgressProps {
  steps: readonly ProposalStep[];
  textBatches: readonly string[];
  onCancel: () => void;
}

export function ProposalStreamProgress({ steps, textBatches, onCancel }: ProposalStreamProgressProps) {
  return (
    <div className="mb-2 flex flex-col gap-2 rounded-md border border-accent/40 bg-accent-soft px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <LoadingSteps steps={steps.length > 0 ? steps : [SENDING_STEP]} />
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Stop
        </Button>
      </div>
      {textBatches.length > 0 ? (
        <div className="flex flex-col gap-1 border-t border-accent/30 pt-2">
          <ProvenanceTag kind="ai" label="AI explanation" className="self-start" />
          <div
            role="log"
            aria-live="polite"
            aria-label="AI explanation"
            data-testid="proposal-stream-text"
            className="max-h-24 overflow-y-auto text-xs leading-5 whitespace-pre-wrap text-fg-secondary"
          >
            {textBatches.map((batch, i) => (
              // Append-only list: the index is a stable identity for each batch.
              <span key={i}>{batch}</span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

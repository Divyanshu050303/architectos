"use client";

/**
 * Structured AI proposal (spec §31–34, §89): recommendation, why, impact, cost,
 * evidence, an explicit diff and its validation. Nothing changes until the user applies
 * it, and the backend creates the new version. Actions are Apply / Edit / Reject (§32):
 * Edit returns the prompt to the command bar to refine and resubmit.
 */
import { X } from "lucide-react";
import { useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";
import { useApplyProposal, useRejectProposal } from "@/hooks/use-proposals";
import { formatCompact, formatCurrency } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import { useCommandStore } from "@/stores/command-store";
import { useUiStore } from "@/stores/ui-store";
import type { Architecture, Proposal, ProposalChange } from "@/types/architecture";

import { conflictFrom } from "../hooks/useArchitectureCommands";
import { focusCommandBar } from "./CommandBar";
import {
  ApplyConfirmDialog,
  blockingFindings,
  ProposalValidationSection,
} from "./proposal/ProposalValidation";

export interface ProposalPanelProps {
  projectId: string;
  proposal: Proposal;
  /** Applying while the draft has unsaved edits would fork history, so it is blocked. */
  isDirty: boolean;
  onApplied: (architecture: Architecture) => void;
  onClose: () => void;
  /**
   * Edit (§32): defaults to putting the original prompt back into the command bar,
   * focused, and closing the proposal without applying or rejecting it.
   */
  onEdit?: (prompt: string) => void;
  className?: string;
}

function editInCommandBar(prompt: string): void {
  useCommandStore.getState().setPrompt(prompt);
  focusCommandBar();
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="label-caps">{title}</h3>
      {children}
    </section>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return formatCompact(value);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

const DIFF_META = {
  add: { symbol: "+", srLabel: "Add", className: "text-accent-fg" },
  remove: { symbol: "−", srLabel: "Remove", className: "text-danger-fg" },
  update: { symbol: "~", srLabel: "Change", className: "text-warning-fg" },
} as const;

function DiffLine({
  kind,
  index,
  children,
}: {
  kind: keyof typeof DIFF_META;
  index: number;
  children: React.ReactNode;
}) {
  const meta = DIFF_META[kind];
  return (
    // .motion-appear (styles/architecture.css) staggers lines in; off for reduced motion.
    <li
      className="motion-appear flex items-baseline gap-2 text-sm"
      style={{ "--motion-index": index } as React.CSSProperties}
    >
      <span aria-hidden className={cn("tabular w-3 shrink-0 font-semibold", meta.className)}>
        {meta.symbol}
      </span>
      <span className="sr-only">{meta.srLabel}: </span>
      <span className="min-w-0 text-fg">{children}</span>
    </li>
  );
}

function ChangeLine({ change, index }: { change: ProposalChange; index: number }) {
  switch (change.op) {
    case "add_node":
      return (
        <DiffLine kind="add" index={index}>
          {change.node.name} <span className="text-muted">· {change.node.technology}</span>
        </DiffLine>
      );
    case "remove_node":
      return (
        <DiffLine kind="remove" index={index}>
          {change.name}
        </DiffLine>
      );
    case "update_node":
      return (
        <DiffLine kind="update" index={index}>
          {change.name} <span className="text-muted">· {change.field}</span>{" "}
          <span className="tabular">
            {formatValue(change.before)} → {formatValue(change.after)}
          </span>
        </DiffLine>
      );
    case "add_edge":
      return (
        <DiffLine kind="add" index={index}>
          {change.sourceName} → {change.targetName}
        </DiffLine>
      );
    case "remove_edge":
      return (
        <DiffLine kind="remove" index={index}>
          {change.sourceName} → {change.targetName}
        </DiffLine>
      );
  }
}

function changeKey(change: ProposalChange, index: number): string {
  switch (change.op) {
    case "add_node":
      return `add_node:${change.node.id}`;
    case "add_edge":
      return `add_edge:${change.edge.id}`;
    case "remove_edge":
      return `remove_edge:${change.edgeId}`;
    case "remove_node":
      return `remove_node:${change.nodeId}`;
    case "update_node":
      return `update_node:${change.nodeId}:${change.field}:${index}`;
  }
}

function WhyButton({ evidenceId, label }: { evidenceId: string; label: string }) {
  return (
    <button
      type="button"
      onClick={() => useUiStore.getState().openEvidence(evidenceId)}
      aria-label={`Why? Evidence for ${label}`}
      className="rounded-sm px-1 text-xs font-medium text-info-fg underline-offset-2 hover:underline"
    >
      Why?
    </button>
  );
}

export function ProposalPanel({
  projectId,
  proposal,
  isDirty,
  onApplied,
  onClose,
  onEdit = editInCommandBar,
  className,
}: ProposalPanelProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const apply = useApplyProposal(projectId);
  const reject = useRejectProposal(projectId);
  const isChange = proposal.kind === "change" && proposal.changes.length > 0;
  const busy = apply.isPending || reject.isPending;
  const settled = proposal.status !== "pending";
  const failure = apply.error ?? reject.error;
  const failureInfo = failure && !conflictFrom(failure, proposal.baseVersion) ? getErrorInfo(failure) : null;

  const blocking = blockingFindings(proposal.validation);

  function requestApply() {
    if (blocking.length > 0) setConfirmOpen(true);
    else handleApply();
  }

  function handleEdit() {
    onClose();
    onEdit(proposal.prompt);
  }

  function handleApply() {
    apply.mutate(
      { proposalId: proposal.id, baseVersion: proposal.baseVersion },
      {
        onSuccess: (architecture) => {
          toast(`Applied as v${architecture.version}`, { tone: "success" });
          onApplied(architecture);
        },
        onError: (error) => {
          const conflict = conflictFrom(error, proposal.baseVersion);
          if (conflict) useUiStore.getState().showConflict(conflict);
        },
      },
    );
  }

  function handleReject() {
    reject.mutate(proposal.id, {
      onSuccess: () => {
        toast("Proposal rejected");
        onClose();
      },
    });
  }

  const cost = proposal.cost;
  const costDelta = cost ? cost.after - cost.before : 0;

  return (
    <article
      aria-label={isChange ? "Architecture change proposal" : "ArchitectOS answer"}
      className={cn(
        "flex max-h-full w-full max-w-md flex-col overflow-hidden rounded-lg border border-accent/50 bg-surface shadow-raised",
        className,
      )}
    >
      <header className="flex items-center gap-2 border-b border-default px-4 py-2.5">
        <ProvenanceTag kind="ai" label={isChange ? "AI proposal" : "AI answer"} />
        <span className="truncate text-xs text-muted" title={proposal.prompt}>
          “{proposal.prompt}”
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close proposal"
          className="ml-auto rounded-sm p-1 text-muted hover:bg-surface-2 hover:text-fg"
        >
          <X aria-hidden className="size-4" />
        </button>
      </header>

      <div className="flex min-h-0 flex-col gap-4 overflow-y-auto px-4 py-3">
        <Section title="Recommendation">
          <p className="text-sm font-medium text-fg">{proposal.recommendation}</p>
        </Section>

        <Section title="Why">
          <p className="text-sm text-fg-secondary">{proposal.reason}</p>
        </Section>

        {isChange && proposal.impact.length > 0 ? (
          <Section title="Impact">
            <ul className="flex flex-col gap-1">
              {proposal.impact.map((impact) => (
                <li key={impact.metric} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-fg-secondary">{impact.metric}</span>
                  <span className="flex items-baseline gap-1">
                    <span className="tabular text-fg">
                      {formatCompact(impact.before)} → {formatCompact(impact.after)}
                      <span className="text-muted"> {impact.unit}</span>
                    </span>
                    {impact.evidenceId ? (
                      <WhyButton evidenceId={impact.evidenceId} label={impact.metric} />
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          </Section>
        ) : null}

        {isChange && cost ? (
          <Section title="Cost">
            <p className="tabular text-sm text-fg">
              {formatCurrency(cost.before)} → {formatCurrency(cost.after)}
              <span className="text-muted"> /month</span>{" "}
              <span className={costDelta > 0 ? "text-warning-fg" : "text-accent-fg"}>
                ({costDelta >= 0 ? "+" : "−"}
                {formatCurrency(Math.abs(costDelta))})
              </span>
            </p>
          </Section>
        ) : null}

        <Section title="Evidence">
          {proposal.evidenceIds.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-surface-2 px-2.5 py-2">
              <span className="text-xs text-fg-secondary">
                <span className="tabular">{proposal.evidenceIds.length}</span>{" "}
                {proposal.evidenceIds.length === 1 ? "item" : "items"}
              </span>
              {proposal.evidenceIds.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => useUiStore.getState().openEvidence(id)}
                  className="tabular rounded-sm border border-default bg-surface px-1.5 text-2xs text-fg-secondary hover:border-strong hover:text-fg"
                >
                  {id}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted">No supporting evidence was returned.</p>
          )}
        </Section>

        {isChange ? (
          <Section title="Changes">
            <ul
              aria-label="Proposed changes"
              className="flex flex-col gap-1 rounded-md border border-default px-2.5 py-2"
            >
              {proposal.changes.map((change, i) => (
                <ChangeLine key={changeKey(change, i)} change={change} index={i} />
              ))}
            </ul>
            <p className="text-2xs text-muted">Previewed on the canvas. Nothing changes until you apply.</p>
          </Section>
        ) : null}

        {isChange ? <ProposalValidationSection validation={proposal.validation} /> : null}

        {failureInfo ? (
          <ErrorState
            title={failureInfo.title}
            message={failureInfo.message}
            requestId={failureInfo.requestId}
            noChangesApplied
          />
        ) : null}
      </div>

      <footer className="flex flex-col gap-2 border-t border-default px-4 py-2.5">
        {settled ? <p className="text-xs text-muted">This proposal was already {proposal.status}.</p> : null}
        {isChange && isDirty && !settled ? (
          <p className="text-xs text-warning-fg">Save or discard your unsaved changes before applying.</p>
        ) : null}
        <div className="flex items-center justify-end gap-2">
          {isChange ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={handleReject}
              loading={reject.isPending}
              disabled={busy || settled}
            >
              Reject
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={onClose} disabled={busy}>
              Close
            </Button>
          )}
          <Button size="sm" variant="secondary" onClick={handleEdit} disabled={busy}>
            Edit
          </Button>
          {isChange ? (
            <Button
              size="sm"
              variant="primary"
              onClick={requestApply}
              loading={apply.isPending}
              disabled={busy || isDirty || settled}
            >
              Apply
            </Button>
          ) : null}
        </div>
      </footer>
      {isChange ? (
        <ApplyConfirmDialog
          open={confirmOpen}
          findings={blocking}
          onOpenChange={setConfirmOpen}
          onConfirm={handleApply}
        />
      ) : null}
    </article>
  );
}

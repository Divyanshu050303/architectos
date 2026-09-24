"use client";

/**
 * AI command bar (spec §30–33, §87–89). A prompt produces a *proposal* for the saved
 * version; nothing here changes the architecture. Progress comes from the backend's
 * streamed steps, with the AI's explanatory text shown as it arrives (§88); the proposal
 * itself arrives complete and opens in the ProposalPanel.
 */
import { X } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { useStreamProposal } from "@/hooks/use-proposals";
import { cn } from "@/lib/utils";
import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { ProposalStreamProgress } from "./proposal/ProposalStreamProgress";

export const COMMAND_INPUT_ID = "architecture-command-input";

/** Focus the prompt, e.g. after "Explain" prefilled it. */
export function focusCommandBar(): void {
  requestAnimationFrame(() => {
    const input = document.getElementById(COMMAND_INPUT_ID);
    if (input instanceof HTMLTextAreaElement) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  });
}

export const PROMPT_EXAMPLES = [
  "Add Redis caching.",
  "What breaks at 10M users?",
  "Simulate PostgreSQL failure.",
  "Reduce infrastructure cost.",
  "Explain this architecture.",
  "Add a queue between these services.",
] as const;

const PLACEHOLDER_ROTATE_MS = 4000;

export interface CommandBarProps {
  projectId: string;
  /** Saved version proposals are based on; null when nothing is saved yet. */
  baseVersion: number | null;
  /** Proposals target a saved version, so an unsaved draft blocks asking. */
  isDirty: boolean;
}

export function CommandBar({ projectId, baseVersion, isDirty }: CommandBarProps) {
  const prompt = useCommandStore((s) => s.prompt);
  const setPrompt = useCommandStore((s) => s.setPrompt);
  const error = useCommandStore((s) => s.error);
  const recentPrompts = useCommandStore((s) => s.recentPrompts);
  const selectedCount = useWorkspaceStore((s) => s.selectedNodeIds.length);
  const stream = useStreamProposal(projectId);
  const pending = stream.isStreaming;

  const hintId = useId();
  const [exampleIndex, setExampleIndex] = useState(0);
  const [recallIndex, setRecallIndex] = useState(-1);

  useEffect(() => {
    if (prompt) return;
    const timer = setInterval(
      () => setExampleIndex((i) => (i + 1) % PROMPT_EXAMPLES.length),
      PLACEHOLDER_ROTATE_MS,
    );
    return () => clearInterval(timer);
  }, [prompt]);

  const trimmed = prompt.trim();
  const blockedReason = isDirty
    ? "Save or discard your changes before asking for a proposal. Proposals are based on a saved version."
    : baseVersion === null
      ? "Generate an architecture before asking for a proposal."
      : null;
  const canSubmit = trimmed.length > 0 && blockedReason === null && !pending;

  function submit(text = trimmed) {
    if (!text || blockedReason !== null || pending || baseVersion === null) return;
    const store = useCommandStore.getState();
    if (store.prompt.trim() !== text) store.setPrompt(text);
    store.submitStarted();
    setRecallIndex(-1);
    void stream.start(
      {
        prompt: text,
        baseVersion,
        selectedNodeIds: useWorkspaceStore.getState().selectedNodeIds,
      },
      {
        onSuccess: (proposal) => useCommandStore.getState().submitSucceeded(proposal.id),
        onError: (err) => {
          const info = getErrorInfo(err);
          useCommandStore.getState().submitFailed({
            title: "The proposal could not be created.",
            message: info.message,
            requestId: info.requestId,
          });
        },
        // Stopped by the user: keep the prompt so it can be refined and resent.
        onCancel: () => useCommandStore.setState({ status: "idle" }),
      },
    );
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape" && pending) {
      event.preventDefault();
      stream.abort();
      return;
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
      return;
    }
    // Recall recent prompts like a shell history, only when the field is empty or recalled.
    if (event.key === "ArrowUp" && (prompt === "" || recallIndex >= 0)) {
      const next = recallIndex + 1;
      const recalled = recentPrompts[next];
      if (recalled !== undefined) {
        event.preventDefault();
        setRecallIndex(next);
        setPrompt(recalled);
      }
    } else if (event.key === "ArrowDown" && recallIndex >= 0) {
      event.preventDefault();
      const next = recallIndex - 1;
      setRecallIndex(next);
      setPrompt(next >= 0 ? (recentPrompts[next] ?? "") : "");
    }
  }

  const dismissError = () => useCommandStore.setState({ status: "idle", error: null });

  return (
    <section aria-label="Ask ArchitectOS" className="shrink-0 border-t border-default bg-surface px-3 py-2.5">
      {pending ? (
        <ProposalStreamProgress
          steps={stream.steps}
          textBatches={stream.textBatches}
          onCancel={stream.abort}
        />
      ) : null}
      {error && !pending ? (
        <div className="relative mb-2">
          <ErrorState
            title={error.title}
            message={error.message}
            requestId={error.requestId}
            noChangesApplied
            onRetry={() => submit(recentPrompts[0] ?? trimmed)}
          />
          <button
            type="button"
            aria-label="Dismiss error"
            onClick={dismissError}
            className="absolute top-2 right-2 rounded-sm p-1 text-muted hover:bg-surface hover:text-fg"
          >
            <X aria-hidden className="size-3.5" />
          </button>
        </div>
      ) : null}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className={cn(
          "flex items-end gap-2 rounded-md border border-strong bg-surface px-3 py-2 transition-colors",
          "focus-within:border-accent-strong focus-within:ring-2 focus-within:ring-accent/20",
        )}
      >
        <label htmlFor={COMMAND_INPUT_ID} className="flex h-7 shrink-0 items-center gap-1.5">
          <span aria-hidden className="text-accent-fg">
            ✦
          </span>
          <span className="text-xs font-medium text-fg-secondary">Ask ArchitectOS</span>
        </label>
        <Textarea
          id={COMMAND_INPUT_ID}
          rows={1}
          value={prompt}
          onChange={(event) => {
            setRecallIndex(-1);
            setPrompt(event.target.value);
          }}
          onKeyDown={onKeyDown}
          placeholder={PROMPT_EXAMPLES[exampleIndex]}
          aria-describedby={blockedReason ? hintId : undefined}
          // The form draws the border and focus ring (focus-within), so the field itself is bare.
          variant="bare"
          className="field-sizing-content max-h-32 min-h-7 flex-1 resize-none py-1"
        />
        {selectedCount > 0 ? (
          <span className="hidden h-7 shrink-0 items-center text-2xs text-muted sm:inline-flex">
            <span className="tabular">{selectedCount}</span>&nbsp;selected
          </span>
        ) : null}
        <Kbd shortcut="mod+enter" className="mb-1 hidden sm:inline-flex" />
        <Button type="submit" size="sm" variant="ai" disabled={!canSubmit} loading={pending}>
          Ask
        </Button>
      </form>
      {blockedReason ? (
        <p id={hintId} className="mt-1.5 text-xs text-warning-fg">
          {blockedReason}
        </p>
      ) : null}
    </section>
  );
}

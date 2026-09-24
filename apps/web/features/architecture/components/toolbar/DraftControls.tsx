"use client";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip } from "@/components/ui/tooltip";

/** Unsaved-changes indicator (spec §22): pending edit count, or the saved version. */
export function DraftStatus({
  pendingCount,
  savedVersion,
}: {
  pendingCount: number;
  savedVersion: number | null;
}) {
  if (pendingCount > 0) {
    return (
      <span role="status" className="inline-flex items-center gap-1.5 text-xs text-warning-fg">
        <span aria-hidden className="size-1.5 rounded-full bg-warning" />
        Unsaved<span className="hidden @min-[64rem]:inline"> changes</span> ·{" "}
        <span className="tabular">{pendingCount}</span>
      </span>
    );
  }
  return (
    <span role="status" className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span aria-hidden className="size-1.5 rounded-full bg-accent-strong" />
      {savedVersion !== null ? (
        <>
          Saved <span className="tabular">v{savedVersion}</span>
        </>
      ) : (
        "Saved"
      )}
    </span>
  );
}

/** Discard with an explicit confirmation, since discarded edits cannot be recovered. */
export function DiscardButton({ pendingCount, onDiscard }: { pendingCount: number; onDiscard: () => void }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost">
          Discard
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="flex w-64 flex-col gap-3">
        <p className="text-sm text-fg">
          Discard <span className="tabular">{pendingCount}</span> unsaved{" "}
          {pendingCount === 1 ? "change" : "changes"}? This cannot be undone.
        </p>
        <div className="flex justify-end">
          <Button size="sm" variant="danger" onClick={onDiscard}>
            Discard changes
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function SaveButton({
  dirty,
  isSaving,
  onSave,
}: {
  dirty: boolean;
  isSaving: boolean;
  onSave: () => void;
}) {
  return (
    <Tooltip content="Save as a new version" shortcut="mod+s">
      <Button size="sm" variant="primary" disabled={!dirty} loading={isSaving} onClick={onSave}>
        Save
      </Button>
    </Tooltip>
  );
}

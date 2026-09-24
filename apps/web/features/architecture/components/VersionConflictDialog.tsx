"use client";

/**
 * Optimistic version conflict (spec §93): never silently overwrite someone else's
 * version. Branching is not built yet, so it is not offered (no dead UI).
 */
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { useUiStore } from "@/stores/ui-store";

export interface VersionConflictDialogProps {
  /** Discard the local draft and load the latest version. */
  onReload: () => void;
  reloading?: boolean;
  /** Compare your version with the latest (spec §43, §93). */
  onCompare: (yourVersion: number, latestVersion: number) => void;
}

export function VersionConflictDialog({
  onReload,
  reloading = false,
  onCompare,
}: VersionConflictDialogProps) {
  const conflict = useUiStore((s) => s.conflict);
  const dismiss = useUiStore((s) => s.dismissConflict);

  return (
    <Dialog open={conflict !== null} onOpenChange={(open) => !open && dismiss()}>
      <DialogContent
        title="Architecture updated"
        description="Someone changed this architecture."
        className="motion-dialog"
      >
        {conflict ? (
          <>
            <dl className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1 rounded-md border border-default bg-surface-2 px-3 py-2">
                <dt className="label-caps">Your version</dt>
                <dd className="tabular text-lg font-semibold text-fg">v{conflict.yourVersion}</dd>
              </div>
              <div className="flex flex-col gap-1 rounded-md border border-default bg-surface-2 px-3 py-2">
                <dt className="label-caps">Latest</dt>
                <dd className="tabular text-lg font-semibold text-fg">v{conflict.latestVersion}</dd>
              </div>
            </dl>
            <p className="mt-3 text-sm text-fg-secondary">
              Nothing was overwritten. Reloading loads the latest version and discards your unsaved changes.
            </p>
          </>
        ) : null}
        <DialogFooter>
          <Button
            variant="secondary"
            disabled={!conflict}
            onClick={() => conflict && onCompare(conflict.yourVersion, conflict.latestVersion)}
          >
            Compare
          </Button>
          <Button variant="primary" onClick={onReload} loading={reloading}>
            Reload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

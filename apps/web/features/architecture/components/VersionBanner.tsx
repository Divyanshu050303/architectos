"use client";

/** Read-only banner shown while a saved version is viewed (`?version=N`, spec §43). */
import { Eye } from "lucide-react";

import { Button } from "@/components/ui/button";

export interface VersionBannerProps {
  version: number;
  latestVersion: number | null;
  loadFailed: boolean;
  onCompareWithLatest: () => void;
  onBackToLatest: () => void;
}

export function VersionBanner({
  version,
  latestVersion,
  loadFailed,
  onCompareWithLatest,
  onBackToLatest,
}: VersionBannerProps) {
  return (
    <div
      role="status"
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-info/40 bg-info-soft px-3 py-1.5 text-xs text-fg"
    >
      <Eye aria-hidden className="size-3.5 text-info-fg" />
      <span>
        Viewing <span className="tabular font-semibold">v{version}</span> (read-only)
        {loadFailed ? <span className="text-danger-fg"> · this version could not be loaded</span> : null}
      </span>
      <span className="ml-auto flex items-center gap-1">
        {latestVersion !== null ? (
          <Button size="sm" variant="ghost" onClick={onCompareWithLatest}>
            Compare with latest
          </Button>
        ) : null}
        <Button size="sm" variant="secondary" onClick={onBackToLatest}>
          Back to latest
        </Button>
      </span>
    </div>
  );
}

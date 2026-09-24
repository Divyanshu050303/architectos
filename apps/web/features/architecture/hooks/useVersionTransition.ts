/**
 * Version transition (spec §100): when a different version replaces the one on screen
 * (a proposal applied, a reload after a conflict, `?version=N`), the components that
 * changed are marked for a short, subtle fade. The mark clears itself afterwards;
 * under reduced motion the CSS never animates it.
 */
import { useEffect, useState } from "react";

import type { Architecture, ArchitectureNode } from "@/types/architecture";

import { changedNodeIds } from "../utils/version-diff";

/** Slightly longer than the CSS animation, so the class is never removed mid-fade. */
export const VERSION_TRANSITION_MS = 1000;

interface Seen {
  key: string | null;
  nodes: readonly ArchitectureNode[] | null;
  changed: ReadonlySet<string> | null;
}

function versionKey(architecture: Architecture | null): string | null {
  return architecture ? `${architecture.projectId}:${architecture.version}` : null;
}

export function useVersionTransition(architecture: Architecture | null): ReadonlySet<string> | null {
  const key = versionKey(architecture);
  const nodes = architecture?.nodes ?? null;
  const [seen, setSeen] = useState<Seen>({ key, nodes, changed: null });

  // Derived during render (not in an effect) so the first frame of the new version is already marked.
  if (seen.key !== key || seen.nodes !== nodes) {
    let changed = seen.changed;
    if (seen.key !== key) {
      const sameProject = seen.key !== null && key !== null && seen.key.split(":")[0] === key.split(":")[0];
      const diff = sameProject && seen.nodes && nodes ? changedNodeIds(seen.nodes, nodes) : null;
      changed = diff && diff.size > 0 ? diff : null;
    }
    setSeen({ key, nodes, changed });
  }

  const changed = seen.changed;
  useEffect(() => {
    if (!changed) return;
    const timer = setTimeout(
      () => setSeen((current) => (current.changed === changed ? { ...current, changed: null } : current)),
      VERSION_TRANSITION_MS,
    );
    return () => clearTimeout(timer);
  }, [changed]);

  return changed;
}

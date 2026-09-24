/**
 * Which components changed between the architecture on screen and the version that
 * replaced it (spec §100 version transition). Layout moves are not changes.
 */
import type { ArchitectureNode } from "@/types/architecture";

function signature(node: ArchitectureNode): string {
  const { id, type, name, technology, description, domain, configuration } = node;
  return JSON.stringify([id, type, name, technology, description ?? null, domain ?? null, configuration]);
}

export function changedNodeIds(
  previous: readonly ArchitectureNode[],
  next: readonly ArchitectureNode[],
): Set<string> {
  const before = new Map(previous.map((n) => [n.id, n]));
  const changed = new Set<string>();
  for (const node of next) {
    const old = before.get(node.id);
    if (!old || (old !== node && signature(old) !== signature(node))) changed.add(node.id);
  }
  return changed;
}

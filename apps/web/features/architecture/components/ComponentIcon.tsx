import { createElement } from "react";

import type { ArchitectureNode } from "@/types/architecture";

import { componentIcon } from "../constants";

export interface ComponentIconProps {
  node: Pick<ArchitectureNode, "type" | "technology" | "name">;
  className?: string;
}

/**
 * The component's recognisable technology icon, or its category icon (spec §99).
 * Decorative: the category and technology are always shown as text next to it.
 */
export function ComponentIcon({ node, className }: ComponentIconProps) {
  // The icon is a stable module-level Lucide component chosen from a lookup table.
  return createElement(componentIcon(node), { "aria-hidden": true, className });
}

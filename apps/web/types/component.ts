import type { z } from "zod";

import type { ComponentTypeSchema } from "@/schemas/architecture";

export type ComponentType = z.infer<typeof ComponentTypeSchema>;

/** Visual state of a node on the canvas (spec §25). */
export type NodeVisualState =
  "default" | "healthy" | "warning" | "critical" | "disabled" | "loading" | "simulating";

/** What a new component looks like when added from the canvas or command palette. */
export interface ComponentDefinition {
  type: ComponentType;
  name: string;
  technology: string;
  configuration: Record<string, unknown>;
}

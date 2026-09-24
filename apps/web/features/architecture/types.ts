import type { ArchitectureEdge, ArchitectureNode, Position } from "@/types/architecture";

/**
 * Semantic architecture commands (spec §106–107). Every edit to an architecture is
 * one of these, so editing is testable, undoable and can be sent to the backend
 * as-is. React Flow never mutates architecture state directly.
 */
export type ArchitectureCommand =
  | { type: "ADD_COMPONENT"; node: ArchitectureNode }
  | { type: "REMOVE_COMPONENTS"; nodeIds: string[] }
  | { type: "CONNECT_COMPONENTS"; edge: ArchitectureEdge }
  | { type: "REMOVE_CONNECTIONS"; edgeIds: string[] }
  | { type: "RENAME_COMPONENT"; nodeId: string; name: string }
  | { type: "UPDATE_CONFIGURATION"; nodeId: string; configuration: Record<string, unknown> }
  | { type: "CHANGE_REPLICAS"; nodeId: string; replicas: number }
  /** Layout only: never creates an architecture version (spec §67, §91). */
  | { type: "MOVE_COMPONENTS"; positions: Record<string, Position> };

export type ArchitectureCommandType = ArchitectureCommand["type"];

export class CommandError extends Error {
  constructor(
    message: string,
    readonly command: ArchitectureCommand,
  ) {
    super(message);
    this.name = "CommandError";
  }
}

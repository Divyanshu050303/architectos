import type { Architecture, ArchitectureNode } from "@/types/architecture";

import { type ArchitectureCommand, CommandError } from "../types";

export function isLayoutCommand(command: ArchitectureCommand): boolean {
  return command.type === "MOVE_COMPONENTS";
}

/** Commands that change what the system *is* and therefore need an explicit save (spec §91–92). */
export function isSemanticCommand(command: ArchitectureCommand): boolean {
  return !isLayoutCommand(command);
}

function requireNode(
  architecture: Architecture,
  nodeId: string,
  command: ArchitectureCommand,
): ArchitectureNode {
  const node = architecture.nodes.find((n) => n.id === nodeId);
  if (!node) throw new CommandError(`Component "${nodeId}" does not exist`, command);
  return node;
}

function updateNode(
  architecture: Architecture,
  nodeId: string,
  update: (node: ArchitectureNode) => ArchitectureNode,
): Architecture {
  return { ...architecture, nodes: architecture.nodes.map((n) => (n.id === nodeId ? update(n) : n)) };
}

/**
 * Pure reducer: returns a new architecture with `command` applied, or throws
 * CommandError if the command is invalid for this architecture. Never mutates input.
 */
export function applyCommand(architecture: Architecture, command: ArchitectureCommand): Architecture {
  switch (command.type) {
    case "ADD_COMPONENT": {
      if (architecture.nodes.some((n) => n.id === command.node.id)) {
        throw new CommandError(`Component "${command.node.id}" already exists`, command);
      }
      return { ...architecture, nodes: [...architecture.nodes, command.node] };
    }
    case "REMOVE_COMPONENTS": {
      const ids = new Set(command.nodeIds);
      for (const id of ids) requireNode(architecture, id, command);
      return {
        ...architecture,
        nodes: architecture.nodes.filter((n) => !ids.has(n.id)),
        edges: architecture.edges.filter((e) => !ids.has(e.source) && !ids.has(e.target)),
      };
    }
    case "CONNECT_COMPONENTS": {
      const { edge } = command;
      requireNode(architecture, edge.source, command);
      requireNode(architecture, edge.target, command);
      if (edge.source === edge.target)
        throw new CommandError("A component cannot connect to itself", command);
      if (architecture.edges.some((e) => e.id === edge.id)) {
        throw new CommandError(`Connection "${edge.id}" already exists`, command);
      }
      if (architecture.edges.some((e) => e.source === edge.source && e.target === edge.target)) {
        throw new CommandError("These components are already connected", command);
      }
      return { ...architecture, edges: [...architecture.edges, edge] };
    }
    case "REMOVE_CONNECTIONS": {
      const ids = new Set(command.edgeIds);
      return { ...architecture, edges: architecture.edges.filter((e) => !ids.has(e.id)) };
    }
    case "RENAME_COMPONENT": {
      const name = command.name.trim();
      if (!name) throw new CommandError("Name cannot be empty", command);
      requireNode(architecture, command.nodeId, command);
      return updateNode(architecture, command.nodeId, (n) => ({ ...n, name }));
    }
    case "UPDATE_CONFIGURATION": {
      requireNode(architecture, command.nodeId, command);
      return updateNode(architecture, command.nodeId, (n) => ({
        ...n,
        configuration: { ...n.configuration, ...command.configuration },
      }));
    }
    case "CHANGE_REPLICAS": {
      if (!Number.isInteger(command.replicas) || command.replicas < 0) {
        throw new CommandError("Replicas must be a non-negative whole number", command);
      }
      requireNode(architecture, command.nodeId, command);
      return updateNode(architecture, command.nodeId, (n) => ({
        ...n,
        configuration: { ...n.configuration, replicas: command.replicas },
      }));
    }
    case "MOVE_COMPONENTS": {
      return {
        ...architecture,
        nodes: architecture.nodes.map((n) => {
          const position = command.positions[n.id];
          return position ? { ...n, position } : n;
        }),
      };
    }
  }
}

export function applyCommands(
  architecture: Architecture,
  commands: readonly ArchitectureCommand[],
): Architecture {
  return commands.reduce(applyCommand, architecture);
}

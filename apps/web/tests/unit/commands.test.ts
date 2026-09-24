import { describe, expect, it } from "vitest";

import { type ArchitectureCommand, CommandError } from "@/features/architecture/types";
import {
  applyCommand,
  applyCommands,
  isLayoutCommand,
  isSemanticCommand,
} from "@/features/architecture/utils/commands";
import type { Architecture, ArchitectureEdge, ArchitectureNode } from "@/types/architecture";

function node(id: string, overrides: Partial<ArchitectureNode> = {}): ArchitectureNode {
  return {
    id,
    type: "service",
    name: id.toUpperCase(),
    technology: "Go",
    configuration: { replicas: 1 },
    position: { x: 0, y: 0 },
    ...overrides,
  };
}

function edge(id: string, source: string, target: string): ArchitectureEdge {
  return { id, source, target, synchronous: true, critical: true };
}

function fixture(): Architecture {
  return {
    id: "arch",
    projectId: "proj",
    version: 1,
    nodes: [node("api"), node("db", { type: "database" }), node("cache", { type: "cache" })],
    edges: [edge("e1", "api", "db"), edge("e2", "api", "cache")],
    assumptions: [],
    createdAt: "2026-09-01T00:00:00.000Z",
    createdBy: "user",
  };
}

function expectCommandError(architecture: Architecture, command: ArchitectureCommand, message: RegExp) {
  let caught: unknown;
  try {
    applyCommand(architecture, command);
  } catch (error) {
    caught = error;
  }
  expect(caught).toBeInstanceOf(CommandError);
  expect((caught as CommandError).message).toMatch(message);
  expect((caught as CommandError).command).toBe(command);
}

describe("architecture commands", () => {
  it("classifies layout vs semantic commands", () => {
    const move: ArchitectureCommand = { type: "MOVE_COMPONENTS", positions: {} };
    const rename: ArchitectureCommand = { type: "RENAME_COMPONENT", nodeId: "api", name: "Gateway" };
    expect(isLayoutCommand(move)).toBe(true);
    expect(isSemanticCommand(move)).toBe(false);
    expect(isSemanticCommand(rename)).toBe(true);
  });

  it("ADD_COMPONENT adds a node and rejects duplicate ids", () => {
    const architecture = fixture();
    const next = applyCommand(architecture, { type: "ADD_COMPONENT", node: node("worker") });
    expect(next.nodes.map((n) => n.id)).toEqual(["api", "db", "cache", "worker"]);
    expectCommandError(architecture, { type: "ADD_COMPONENT", node: node("api") }, /already exists/);
  });

  it("REMOVE_COMPONENTS removes nodes with their edges and rejects unknown ids", () => {
    const architecture = fixture();
    const next = applyCommand(architecture, { type: "REMOVE_COMPONENTS", nodeIds: ["db"] });
    expect(next.nodes.map((n) => n.id)).toEqual(["api", "cache"]);
    expect(next.edges.map((e) => e.id)).toEqual(["e2"]);
    expectCommandError(architecture, { type: "REMOVE_COMPONENTS", nodeIds: ["nope"] }, /does not exist/);
  });

  it("CONNECT_COMPONENTS adds an edge and validates it", () => {
    const architecture = fixture();
    const next = applyCommand(architecture, { type: "CONNECT_COMPONENTS", edge: edge("e3", "cache", "db") });
    expect(next.edges.map((e) => e.id)).toEqual(["e1", "e2", "e3"]);
    expectCommandError(
      architecture,
      { type: "CONNECT_COMPONENTS", edge: edge("e3", "api", "ghost") },
      /does not exist/,
    );
    expectCommandError(
      architecture,
      { type: "CONNECT_COMPONENTS", edge: edge("e3", "api", "api") },
      /cannot connect to itself/,
    );
    expectCommandError(
      architecture,
      { type: "CONNECT_COMPONENTS", edge: edge("e1", "db", "cache") },
      /Connection "e1" already exists/,
    );
    expectCommandError(
      architecture,
      { type: "CONNECT_COMPONENTS", edge: edge("e9", "api", "db") },
      /already connected/,
    );
  });

  it("REMOVE_CONNECTIONS removes edges and ignores unknown ids", () => {
    const next = applyCommand(fixture(), { type: "REMOVE_CONNECTIONS", edgeIds: ["e1", "missing"] });
    expect(next.edges.map((e) => e.id)).toEqual(["e2"]);
  });

  it("RENAME_COMPONENT trims names and rejects empty names or unknown nodes", () => {
    const architecture = fixture();
    const next = applyCommand(architecture, { type: "RENAME_COMPONENT", nodeId: "api", name: "  Gateway " });
    expect(next.nodes[0]?.name).toBe("Gateway");
    expectCommandError(
      architecture,
      { type: "RENAME_COMPONENT", nodeId: "api", name: "   " },
      /cannot be empty/,
    );
    expectCommandError(
      architecture,
      { type: "RENAME_COMPONENT", nodeId: "nope", name: "X" },
      /does not exist/,
    );
  });

  it("UPDATE_CONFIGURATION merges configuration", () => {
    const architecture = fixture();
    const next = applyCommand(architecture, {
      type: "UPDATE_CONFIGURATION",
      nodeId: "api",
      configuration: { timeoutMs: 2000 },
    });
    expect(next.nodes[0]?.configuration).toEqual({ replicas: 1, timeoutMs: 2000 });
    expectCommandError(
      architecture,
      { type: "UPDATE_CONFIGURATION", nodeId: "nope", configuration: {} },
      /does not exist/,
    );
  });

  it("CHANGE_REPLICAS sets replicas and rejects invalid counts", () => {
    const architecture = fixture();
    const next = applyCommand(architecture, { type: "CHANGE_REPLICAS", nodeId: "db", replicas: 2 });
    expect(next.nodes[1]?.configuration.replicas).toBe(2);
    expectCommandError(architecture, { type: "CHANGE_REPLICAS", nodeId: "db", replicas: -1 }, /non-negative/);
    expectCommandError(
      architecture,
      { type: "CHANGE_REPLICAS", nodeId: "db", replicas: 1.5 },
      /whole number/,
    );
    expectCommandError(
      architecture,
      { type: "CHANGE_REPLICAS", nodeId: "nope", replicas: 2 },
      /does not exist/,
    );
  });

  it("MOVE_COMPONENTS updates positions and ignores unknown ids", () => {
    const next = applyCommand(fixture(), {
      type: "MOVE_COMPONENTS",
      positions: { api: { x: 10, y: 20 }, ghost: { x: 1, y: 1 } },
    });
    expect(next.nodes[0]?.position).toEqual({ x: 10, y: 20 });
    expect(next.nodes[1]?.position).toEqual({ x: 0, y: 0 });
  });

  it("never mutates its input", () => {
    const architecture = fixture();
    const snapshot = structuredClone(architecture);
    const commands: ArchitectureCommand[] = [
      { type: "ADD_COMPONENT", node: node("worker") },
      { type: "CONNECT_COMPONENTS", edge: edge("e3", "worker", "db") },
      { type: "RENAME_COMPONENT", nodeId: "api", name: "Gateway" },
      { type: "UPDATE_CONFIGURATION", nodeId: "api", configuration: { timeoutMs: 1 } },
      { type: "CHANGE_REPLICAS", nodeId: "db", replicas: 3 },
      { type: "MOVE_COMPONENTS", positions: { api: { x: 5, y: 5 } } },
      { type: "REMOVE_CONNECTIONS", edgeIds: ["e2"] },
      { type: "REMOVE_COMPONENTS", nodeIds: ["cache"] },
    ];
    const next = applyCommands(architecture, commands);
    expect(architecture).toEqual(snapshot);
    expect(next).not.toBe(architecture);
    expect(next.nodes.map((n) => n.id)).toEqual(["api", "db", "worker"]);
    expect(next.edges.map((e) => e.id)).toEqual(["e1", "e3"]);
  });

  it("applyCommands stops at the first invalid command", () => {
    expect(() =>
      applyCommands(fixture(), [
        { type: "RENAME_COMPONENT", nodeId: "api", name: "Gateway" },
        { type: "RENAME_COMPONENT", nodeId: "ghost", name: "X" },
      ]),
    ).toThrow(CommandError);
  });
});

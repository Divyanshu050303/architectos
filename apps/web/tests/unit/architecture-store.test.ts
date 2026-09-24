import { beforeEach, describe, expect, it } from "vitest";

import {
  MAX_HISTORY,
  selectCanRedo,
  selectCanUndo,
  selectIsDirty,
  selectLayoutChanged,
  useArchitectureStore,
} from "@/stores/architecture-store";
import type { Architecture, ArchitectureNode } from "@/types/architecture";

function node(id: string, name: string, x = 0): ArchitectureNode {
  return {
    id,
    type: "service",
    name,
    technology: "Node",
    configuration: { replicas: 1 },
    position: { x, y: 0 },
  };
}

function architecture(overrides: Partial<Architecture> = {}): Architecture {
  return {
    id: "arch_1",
    projectId: "proj_1",
    version: 1,
    nodes: [node("api", "API"), node("db", "Postgres", 300)],
    edges: [],
    assumptions: [],
    createdAt: "2026-01-01T00:00:00Z",
    createdBy: "user",
    ...overrides,
  };
}

const store = () => useArchitectureStore.getState();

describe("architecture draft store", () => {
  beforeEach(() => {
    store().reset();
    store().load(architecture());
  });

  it("applies semantic commands, records them as pending and marks the draft dirty", () => {
    const result = store().dispatch({ type: "RENAME_COMPONENT", nodeId: "api", name: "Edge API" });

    expect(result).toEqual({ ok: true });
    expect(store().present?.nodes[0]?.name).toBe("Edge API");
    expect(store().pending).toHaveLength(1);
    expect(selectIsDirty(store())).toBe(true);
    expect(selectCanUndo(store())).toBe(true);
  });

  it("returns an error and leaves the draft untouched for invalid commands", () => {
    const result = store().dispatch({ type: "RENAME_COMPONENT", nodeId: "missing", name: "X" });

    expect(result.ok).toBe(false);
    expect(store().lastError).toMatch(/does not exist/);
    expect(store().pending).toHaveLength(0);
    expect(selectCanUndo(store())).toBe(false);
  });

  it("keeps layout moves out of pending but makes them undoable", () => {
    store().dispatch({ type: "MOVE_COMPONENTS", positions: { api: { x: 50, y: 60 } } });

    expect(store().pending).toHaveLength(0);
    expect(selectIsDirty(store())).toBe(false);
    expect(selectLayoutChanged(store())).toBe(true);

    store().undo();
    expect(selectLayoutChanged(store())).toBe(false);
  });

  it("undoes and redoes, restoring pending commands with each snapshot", () => {
    store().dispatch({ type: "CHANGE_REPLICAS", nodeId: "db", replicas: 3 });
    store().dispatch({ type: "RENAME_COMPONENT", nodeId: "db", name: "Primary DB" });

    store().undo();
    expect(store().present?.nodes[1]?.name).toBe("Postgres");
    expect(store().pending).toHaveLength(1);
    expect(selectCanRedo(store())).toBe(true);

    store().undo();
    expect(selectIsDirty(store())).toBe(false);

    store().redo();
    store().redo();
    expect(store().present?.nodes[1]?.name).toBe("Primary DB");
    expect(store().present?.nodes[1]?.configuration.replicas).toBe(3);
    expect(store().pending).toHaveLength(2);
    expect(selectCanRedo(store())).toBe(false);
  });

  it("clears the redo stack when a new command is dispatched", () => {
    store().dispatch({ type: "RENAME_COMPONENT", nodeId: "api", name: "A" });
    store().undo();
    store().dispatch({ type: "RENAME_COMPONENT", nodeId: "api", name: "B" });

    expect(selectCanRedo(store())).toBe(false);
  });

  it("caps history at the maximum size", () => {
    for (let i = 0; i < MAX_HISTORY + 5; i += 1) {
      store().dispatch({ type: "MOVE_COMPONENTS", positions: { api: { x: i, y: 0 } } });
    }
    expect(store().past).toHaveLength(MAX_HISTORY);
  });

  it("markSaved adopts the server architecture and clears pending and history", () => {
    store().dispatch({ type: "RENAME_COMPONENT", nodeId: "api", name: "Edge API" });
    const saved = architecture({ version: 2, nodes: store().present?.nodes ?? [] });

    store().markSaved(saved);

    expect(store().base).toBe(saved);
    expect(store().present).toBe(saved);
    expect(selectIsDirty(store())).toBe(false);
    expect(selectCanUndo(store())).toBe(false);
  });

  it("discard returns to the last server architecture", () => {
    store().dispatch({ type: "REMOVE_COMPONENTS", nodeIds: ["db"] });
    store().discard();

    expect(store().present?.nodes).toHaveLength(2);
    expect(selectIsDirty(store())).toBe(false);
    expect(selectCanUndo(store())).toBe(false);
  });

  it("does not overwrite unsaved edits when a newer server version arrives", () => {
    store().dispatch({ type: "RENAME_COMPONENT", nodeId: "api", name: "Mine" });
    store().load(architecture({ version: 2 }));

    expect(store().present?.nodes[0]?.name).toBe("Mine");

    store().load(architecture({ version: 2 }), { force: true });
    expect(store().present?.version).toBe(2);
    expect(selectIsDirty(store())).toBe(false);
  });

  it("adopts a newer server version when the draft is clean", () => {
    store().load(architecture({ version: 3 }));
    expect(store().present?.version).toBe(3);
  });

  it("replaces the draft when switching projects, even with unsaved edits", () => {
    store().dispatch({ type: "RENAME_COMPONENT", nodeId: "api", name: "Mine" });
    store().load(architecture({ projectId: "proj_2" }));

    expect(store().projectId).toBe("proj_2");
    expect(selectIsDirty(store())).toBe(false);
  });
});

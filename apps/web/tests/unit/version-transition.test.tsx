import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePresence } from "@/features/architecture/hooks/usePresence";
import {
  useVersionTransition,
  VERSION_TRANSITION_MS,
} from "@/features/architecture/hooks/useVersionTransition";
import { changedNodeIds } from "@/features/architecture/utils/version-diff";
import type { Architecture, ArchitectureNode } from "@/types/architecture";

import { foodArchitecture } from "../component/operate-test-utils";

function node(id: string, overrides: Partial<ArchitectureNode> = {}): ArchitectureNode {
  return {
    id,
    type: "service",
    name: id,
    technology: "Go",
    configuration: {},
    position: { x: 0, y: 0 },
    ...overrides,
  };
}

describe("changedNodeIds", () => {
  it("reports added and edited components, not moves or removals", () => {
    const before = [node("a"), node("b"), node("c"), node("gone")];
    const after = [
      node("a"),
      node("b", { configuration: { replicas: 3 } }),
      node("c", { position: { x: 400, y: 10 } }),
      node("new"),
    ];
    expect([...changedNodeIds(before, after)].sort()).toEqual(["b", "new"]);
  });
});

describe("useVersionTransition", () => {
  afterEach(() => vi.useRealTimers());

  function version(base: Architecture, v: number, nodes: ArchitectureNode[]): Architecture {
    return { ...base, version: v, nodes };
  }

  it("marks the components changed by a new version, then clears the mark", () => {
    vi.useFakeTimers();
    const base = foodArchitecture();
    const first = version(base, 3, [node("a"), node("b")]);
    const { result, rerender } = renderHook(({ arch }) => useVersionTransition(arch), {
      initialProps: { arch: first },
    });
    // Nothing fades on first load.
    expect(result.current).toBeNull();

    // Edits to the draft within a version are not a version transition.
    rerender({ arch: { ...first, nodes: [node("a", { name: "A2" }), node("b")] } });
    expect(result.current).toBeNull();

    rerender({
      arch: version(base, 4, [node("a", { name: "A2" }), node("b", { technology: "Rust" }), node("c")]),
    });
    expect(result.current && [...result.current].sort()).toEqual(["b", "c"]);

    act(() => vi.advanceTimersByTime(VERSION_TRANSITION_MS));
    expect(result.current).toBeNull();
  });

  it("does not animate a switch to another project", () => {
    const base = foodArchitecture();
    const { result, rerender } = renderHook(({ arch }) => useVersionTransition(arch), {
      initialProps: { arch: version(base, 1, [node("a")]) },
    });
    rerender({ arch: { ...version(base, 2, [node("z")]), projectId: "other" } });
    expect(result.current).toBeNull();
  });
});

describe("usePresence", () => {
  afterEach(() => vi.useRealTimers());

  it("stays mounted for the exit animation, then unmounts", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ open }) => usePresence(open, 140), {
      initialProps: { open: true },
    });
    expect(result.current).toEqual({ mounted: true, state: "open" });
    rerender({ open: false });
    expect(result.current).toEqual({ mounted: true, state: "closed" });
    act(() => vi.advanceTimersByTime(140));
    expect(result.current.mounted).toBe(false);
    rerender({ open: true });
    expect(result.current).toEqual({ mounted: true, state: "open" });
  });
});

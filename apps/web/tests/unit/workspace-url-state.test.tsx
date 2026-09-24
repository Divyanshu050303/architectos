import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyParamPatch,
  mergePatches,
  parseHighlight,
  parseMode,
  parseVersion,
  parseView,
  resolveView,
  useWorkspaceUrlState,
} from "@/features/architecture/hooks/useWorkspaceUrlState";
import { useArchitectureStore } from "@/stores/architecture-store";
import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { foodArchitecture } from "../component/operate-test-utils";

const nav = vi.hoisted(() => ({
  search: "",
  replaces: [] as string[],
  listeners: new Set<() => void>(),
}));

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (listener: () => void) => {
    nav.listeners.add(listener);
    return () => nav.listeners.delete(listener);
  };
  return {
    usePathname: () => "/project/proj_food/architecture",
    useRouter: () => ({
      push: () => {},
      replace: (url: string) => {
        nav.replaces.push(url);
        nav.search = url.split("?")[1] ?? "";
        window.history.replaceState(null, "", url);
        for (const listener of nav.listeners) listener();
      },
    }),
    useSearchParams: () => new URLSearchParams(useSyncExternalStore(subscribe, () => nav.search)),
  };
});

function setUrl(search: string) {
  nav.search = search;
  window.history.replaceState(null, "", `/project/proj_food/architecture${search ? `?${search}` : ""}`);
}

/** Let queued microtasks (batched URL patches) run. */
async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("URL state parsing", () => {
  it("parses the analysis mode, falling back to topology", () => {
    expect(parseMode("capacity")).toBe("capacity");
    expect(parseMode("security")).toBe("security");
    expect(parseMode(null)).toBe("topology");
    expect(parseMode("bogus")).toBe("topology");
  });

  it("parses the graph view", () => {
    expect(parseView("overview")).toBe("overview");
    expect(parseView("focused")).toBe("focused");
    expect(parseView("sideways")).toBeNull();
    expect(parseView(null)).toBeNull();
  });

  it("accepts only positive integer versions", () => {
    expect(parseVersion("3")).toBe(3);
    expect(parseVersion(null)).toBeNull();
    expect(parseVersion("")).toBeNull();
    expect(parseVersion("0")).toBeNull();
    expect(parseVersion("-2")).toBeNull();
    expect(parseVersion("1.5")).toBeNull();
    expect(parseVersion("abc")).toBeNull();
  });

  it("splits and trims highlight ids", () => {
    expect(parseHighlight("a, b,,c ")).toEqual(["a", "b", "c"]);
    expect(parseHighlight(null)).toEqual([]);
  });

  it("lets an explicit view win, except an overview without domains", () => {
    expect(resolveView(null, "detailed", true)).toBe("detailed");
    expect(resolveView("focused", "overview", true)).toBe("focused");
    expect(resolveView("overview", "detailed", true)).toBe("overview");
    expect(resolveView("overview", "detailed", false)).toBe("detailed");
  });
});

describe("URL state serialisation", () => {
  it("sets, replaces and removes params while keeping the others", () => {
    expect(applyParamPatch("?mode=cost&node=api", { node: "db", view: "focused" })).toBe(
      "mode=cost&node=db&view=focused",
    );
    expect(applyParamPatch("mode=cost&node=api", { node: null, mode: "" })).toBe("");
    expect(applyParamPatch("", { highlight: "a,b" })).toBe("highlight=a%2Cb");
  });

  it("merges patches from the same tick, later keys winning", () => {
    expect(mergePatches({ node: "api", mode: "cost" }, { node: null, view: "overview" })).toEqual({
      node: null,
      mode: "cost",
      view: "overview",
    });
  });
});

describe("useWorkspaceUrlState", () => {
  beforeEach(() => {
    nav.replaces.length = 0;
    nav.listeners.clear();
    setUrl("");
    useWorkspaceStore.getState().reset();
    useCommandStore.getState().reset();
    useArchitectureStore.getState().reset();
    useArchitectureStore.getState().load(foodArchitecture());
  });

  afterEach(() => {
    setUrl("");
  });

  it("reads mode, domain, simulation and version from the URL", () => {
    setUrl("mode=reliability&view=overview&domain=orders&simulation=run_1&version=2");
    const { result } = renderHook(() => useWorkspaceUrlState({ ready: true }));
    expect(result.current.mode).toBe("reliability");
    expect(result.current.simulationRunId).toBe("run_1");
    expect(result.current.version).toBe(2);
    expect(result.current.view).toBe("overview");
    expect(result.current.expandedDomain).toBe("orders");
  });

  it("selects ?node and opens the inspector", () => {
    const nodeId = foodArchitecture().nodes[0]?.id ?? "";
    setUrl(`node=${nodeId}`);
    renderHook(() => useWorkspaceUrlState({ ready: true }));
    expect(useWorkspaceStore.getState().selectedNodeIds).toEqual([nodeId]);
    expect(useWorkspaceStore.getState().inspectorOpen).toBe(true);
  });

  it("ignores ?node and ?highlight ids that do not exist", () => {
    const real = foodArchitecture().nodes[1]?.id ?? "";
    setUrl(`node=ghost&highlight=ghost,${real}`);
    renderHook(() => useWorkspaceUrlState({ ready: true }));
    expect(useWorkspaceStore.getState().selectedNodeIds).toEqual([]);
    expect(useWorkspaceStore.getState().highlightedNodeIds).toEqual([real]);
  });

  it("writes the selection back to ?node", async () => {
    const nodeId = foodArchitecture().nodes[2]?.id ?? "";
    renderHook(() => useWorkspaceUrlState({ ready: true }));
    await flush();
    act(() => useWorkspaceStore.getState().select({ nodeIds: [nodeId] }));
    await flush();
    expect(new URLSearchParams(nav.search).get("node")).toBe(nodeId);
  });

  it("syncs ?proposal both ways", async () => {
    setUrl("proposal=prop_9");
    renderHook(() => useWorkspaceUrlState({ ready: true }));
    expect(useCommandStore.getState().activeProposalId).toBe("prop_9");
    act(() => useCommandStore.getState().clearProposal());
    await flush();
    expect(new URLSearchParams(nav.search).has("proposal")).toBe(false);
  });

  it("merges several setters in one tick into a single replace", async () => {
    setUrl("mode=cost&view=overview&domain=orders");
    const { result } = renderHook(() => useWorkspaceUrlState({ ready: true }));
    await flush();
    nav.replaces.length = 0;
    act(() => {
      result.current.setMode("security");
      result.current.setView("focused");
      result.current.setVersion(2);
    });
    await flush();
    expect(nav.replaces).toHaveLength(1);
    const params = new URLSearchParams(nav.search);
    expect(params.get("mode")).toBe("security");
    expect(params.get("view")).toBe("focused");
    // Leaving the overview drops the expanded domain.
    expect(params.has("domain")).toBe(false);
    expect(params.get("version")).toBe("2");
  });

  it("drops default values from the URL", async () => {
    setUrl("mode=cost&simulation=run_1&version=3");
    const { result } = renderHook(() => useWorkspaceUrlState({ ready: true }));
    act(() => {
      result.current.setMode("topology");
      result.current.setSimulation(null);
      result.current.setVersion(null);
    });
    await flush();
    expect(nav.search).toBe("");
  });

  it("opens the comparison once for ?compare and removes the param", async () => {
    setUrl("compare=1");
    renderHook(() => useWorkspaceUrlState({ ready: true }));
    await flush();
    const version = foodArchitecture().version;
    expect(useWorkspaceStore.getState().compare).toEqual({
      from: version > 1 ? version - 1 : null,
      to: version,
    });
    expect(new URLSearchParams(nav.search).has("compare")).toBe(false);
  });

  it("clears ?highlight with the store highlight", async () => {
    const real = foodArchitecture().nodes[0]?.id ?? "";
    setUrl(`highlight=${real}`);
    const { result } = renderHook(() => useWorkspaceUrlState({ ready: true }));
    expect(useWorkspaceStore.getState().highlightedNodeIds).toEqual([real]);
    act(() => result.current.clearHighlight());
    await flush();
    expect(useWorkspaceStore.getState().highlightedNodeIds).toEqual([]);
    expect(new URLSearchParams(nav.search).has("highlight")).toBe(false);
  });
});

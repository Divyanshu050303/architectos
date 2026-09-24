/**
 * Deep-linkable workspace state (spec §52):
 *   ?node=<id>          selects a component and opens the inspector
 *   ?mode=<mode>        analysis overlay (spec §68)
 *   ?view=<view>        overview | detailed | focused (spec §65); default depends on size
 *   ?domain=<domain>    the domain expanded in the overview
 *   ?simulation=<runId> simulation run replayed by the simulation overlay (default: latest)
 *   ?version=<n>        shows a saved version read-only (e.g. from the evolution timeline)
 *   ?compare=1          opens the version comparison once (from the global palette)
 *   ?highlight=a,b      highlights components and focuses the viewport on them
 *   ?proposal=<id>      opens a proposal held in the query cache
 * URL and stores are kept in sync with router.replace, so no history entries pile up.
 */
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";

import { useArchitectureStore } from "@/stores/architecture-store";
import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import { type AnalysisMode, ANALYSIS_MODES, V1_ANALYSIS_MODES } from "@/types/architecture";

import { defaultGraphView, GRAPH_VIEWS, type GraphView, hasDomains } from "../utils/graph-view";

/** Params to set; null (or "") removes one. */
export type ParamPatch = Record<string, string | null>;

export function parseView(value: string | null): GraphView | null {
  return GRAPH_VIEWS.find((v) => v === value) ?? null;
}

/** Unknown or not-yet-available overlays fall back to topology. */
export function parseMode(value: string | null): AnalysisMode {
  const mode = ANALYSIS_MODES.find((m) => m === value);
  return mode && V1_ANALYSIS_MODES.has(mode) ? mode : "topology";
}

/** A positive integer version, else null. */
export function parseVersion(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;
  const version = Number(value);
  return Number.isInteger(version) && version > 0 ? version : null;
}

/** `?highlight=a, b,,c` → ["a", "b", "c"]. */
export function parseHighlight(value: string | null): string[] {
  return (value ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
}

/**
 * The view to show: an explicit `?view` wins, except "overview" without domains;
 * otherwise the default for the architecture's size.
 */
export function resolveView(requested: GraphView | null, defaultView: GraphView, domainsAvailable: boolean) {
  return requested === "overview" && !domainsAvailable ? "detailed" : (requested ?? defaultView);
}

/** Merge a later patch into an earlier one made in the same tick (later keys win). */
export function mergePatches(first: ParamPatch, second: ParamPatch): ParamPatch {
  return { ...first, ...second };
}

/** Apply a patch to a query string; returns the new query without "?" (order of untouched keys kept). */
export function applyParamPatch(search: string, patch: ParamPatch): string {
  const params = new URLSearchParams(search);
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === "") params.delete(key);
    else params.set(key, value);
  }
  return params.toString();
}

function nodeExists(id: string): boolean {
  return useArchitectureStore.getState().present?.nodes.some((n) => n.id === id) ?? false;
}

export function useWorkspaceUrlState({ ready }: { ready: boolean }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const nodeParam = searchParams.get("node");
  const highlightParam = searchParams.get("highlight");
  const proposalParam = searchParams.get("proposal");
  const mode = parseMode(searchParams.get("mode"));
  const domainParam = searchParams.get("domain");
  const simulationParam = searchParams.get("simulation");
  const versionParam = parseVersion(searchParams.get("version"));

  // Default view follows the architecture's size; an explicit ?view always wins.
  const { nodeCount, domainsAvailable } = useArchitectureStore(
    useShallow((s) => ({
      nodeCount: s.present?.nodes.length ?? 0,
      domainsAvailable: s.present ? hasDomains(s.present.nodes) : false,
    })),
  );
  const defaultView = defaultGraphView(nodeCount, domainsAvailable);
  const requestedView = parseView(searchParams.get("view"));
  const view: GraphView = resolveView(requestedView, defaultView, domainsAvailable);

  // Patches made in the same tick are merged into one replace, so effects never clobber each other.
  const pendingPatch = useRef<ParamPatch | null>(null);
  const updateParams = useCallback(
    (patch: ParamPatch) => {
      if (pendingPatch.current) {
        pendingPatch.current = mergePatches(pendingPatch.current, patch);
        return;
      }
      pendingPatch.current = { ...patch };
      queueMicrotask(() => {
        const next = pendingPatch.current ?? {};
        pendingPatch.current = null;
        const query = applyParamPatch(window.location.search, next);
        if (query === window.location.search.replace(/^\?/, "")) return;
        router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
      });
    },
    [router, pathname],
  );

  const setMode = useCallback(
    (next: AnalysisMode) => updateParams({ mode: next === "topology" ? null : next }),
    [updateParams],
  );

  const setView = useCallback(
    (next: GraphView) =>
      updateParams({
        view: next === defaultView ? null : next,
        ...(next !== "overview" ? { domain: null } : {}),
      }),
    [updateParams, defaultView],
  );

  const setDomain = useCallback((domain: string | null) => updateParams({ domain }), [updateParams]);

  const setSimulation = useCallback(
    (runId: string | null) => updateParams({ simulation: runId }),
    [updateParams],
  );

  const setVersion = useCallback(
    (version: number | null) => updateParams({ version: version?.toString() ?? null }),
    [updateParams],
  );

  // --- ?node ---------------------------------------------------------------
  const nodeSynced = useRef(false);
  useEffect(() => {
    if (!ready) return;
    const ws = useWorkspaceStore.getState();
    if (nodeParam && nodeExists(nodeParam)) {
      const current = ws.selectedNodeIds;
      if (!(current.length === 1 && current[0] === nodeParam)) {
        ws.select({ nodeIds: [nodeParam] });
        ws.openInspector();
      }
    }
    nodeSynced.current = true;
  }, [ready, nodeParam]);

  const selectedNodeIds = useWorkspaceStore((s) => s.selectedNodeIds);
  useEffect(() => {
    if (!nodeSynced.current) return;
    // Read the store, not the render value: the URL effect above may have just selected.
    const ids = useWorkspaceStore.getState().selectedNodeIds;
    const desired = ids.length === 1 ? (ids[0] ?? null) : null;
    if (desired !== nodeParam) updateParams({ node: desired });
  }, [selectedNodeIds, nodeParam, updateParams]);

  // --- ?highlight ----------------------------------------------------------
  useEffect(() => {
    if (!ready || !highlightParam) return;
    const ids = parseHighlight(highlightParam).filter(nodeExists);
    if (ids.length > 0) useWorkspaceStore.getState().highlight(ids);
  }, [ready, highlightParam]);

  const clearHighlight = useCallback(() => {
    useWorkspaceStore.getState().clearHighlight();
    updateParams({ highlight: null });
  }, [updateParams]);

  // --- ?compare (one-shot) --------------------------------------------------
  const compareParam = searchParams.get("compare");
  useEffect(() => {
    if (!ready || !compareParam) return;
    const version = useArchitectureStore.getState().base?.version ?? null;
    useWorkspaceStore.getState().openCompare(version !== null && version > 1 ? version - 1 : null, version);
    updateParams({ compare: null });
  }, [ready, compareParam, updateParams]);

  // --- ?proposal -----------------------------------------------------------
  const proposalSynced = useRef(false);
  useEffect(() => {
    const store = useCommandStore.getState();
    if (proposalParam && store.activeProposalId !== proposalParam) {
      useCommandStore.setState({ activeProposalId: proposalParam });
    }
    proposalSynced.current = true;
  }, [proposalParam]);

  const activeProposalId = useCommandStore((s) => s.activeProposalId);
  useEffect(() => {
    if (!proposalSynced.current) return;
    const desired = useCommandStore.getState().activeProposalId;
    if (desired !== proposalParam) updateParams({ proposal: desired });
  }, [activeProposalId, proposalParam, updateParams]);

  return {
    mode,
    setMode,
    view,
    setView,
    expandedDomain: view === "overview" ? domainParam : null,
    setDomain,
    simulationRunId: simulationParam,
    setSimulation,
    version: versionParam,
    setVersion,
    clearHighlight,
  };
}

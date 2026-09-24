import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  applyProposal,
  createProposal,
  fixFinding,
  type ProposalRequest,
  type ProposalStep,
  rejectProposal,
  streamProposal,
} from "@/api/proposals";
import { track } from "@/lib/analytics";
import { queryKeys } from "@/lib/query-keys";
import type { Architecture, Proposal } from "@/types/architecture";

import { invalidateArchitectureDependents } from "./use-architecture";

/**
 * AI proposals are never applied optimistically (spec §31, §50, §89): the architecture
 * changes only after the backend applies an approved proposal.
 */
/**
 * Proposals have no GET endpoint, so created proposals are kept in the query cache
 * under queryKeys.proposal(id) and read back with useProposal(id). This is how the
 * validation page's "Fix" hands a proposal to the architecture workspace.
 */
export function useProposal(proposalId: string | null) {
  return useQuery<Proposal | null>({
    queryKey: queryKeys.proposal(proposalId ?? "none"),
    queryFn: () => null,
    enabled: false,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
  });
}

export function useCreateProposal(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ProposalRequest) => createProposal(projectId, input),
    onSuccess: (proposal) => queryClient.setQueryData(queryKeys.proposal(proposal.id), proposal),
  });
}

/** Streamed text is rendered in batches so a polite live region is not spammed per token. */
export const STREAM_TEXT_FLUSH_MS = 400;

export interface ProposalStreamCallbacks {
  onSuccess: (proposal: Proposal) => void;
  onError: (error: unknown) => void;
  /** The user (or an unmount) aborted the stream; nothing was created client-side. */
  onCancel?: () => void;
}

interface StreamState {
  isStreaming: boolean;
  steps: ProposalStep[];
  /** Append-only batches of explanatory text. */
  textBatches: string[];
}

const IDLE_STREAM: StreamState = { isStreaming: false, steps: [], textBatches: [] };

function upsertStep(steps: readonly ProposalStep[], step: ProposalStep): ProposalStep[] {
  const index = steps.findIndex((s) => s.id === step.id);
  if (index < 0) return [...steps, step];
  return steps.map((s, i) => (i === index ? step : s));
}

/**
 * Request a proposal over the streaming endpoint (spec §87–88): real progress steps and
 * batched explanatory text while it is drafted. The proposal itself arrives atomically
 * and is only cached here; nothing touches the architecture (§31, §89).
 */
export function useStreamProposal(projectId: string) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<StreamState>(IDLE_STREAM);
  const controllerRef = useRef<AbortController | null>(null);
  const pendingTextRef = useRef("");
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearFlush = useCallback(() => {
    if (flushTimerRef.current !== null) clearTimeout(flushTimerRef.current);
    flushTimerRef.current = null;
    pendingTextRef.current = "";
  }, []);

  const abort = useCallback(() => {
    controllerRef.current?.abort(new DOMException("The proposal request was cancelled.", "AbortError"));
  }, []);

  useEffect(
    () => () => {
      abort();
      clearFlush();
    },
    [abort, clearFlush],
  );

  const start = useCallback(
    async (input: ProposalRequest, callbacks: ProposalStreamCallbacks) => {
      controllerRef.current?.abort(new DOMException("Superseded by a new request.", "AbortError"));
      clearFlush();
      const controller = new AbortController();
      controllerRef.current = controller;
      const isCurrent = () => controllerRef.current === controller && !controller.signal.aborted;
      const update = (fn: (s: StreamState) => StreamState) => {
        if (isCurrent()) setState(fn);
      };
      const flush = () => {
        flushTimerRef.current = null;
        const text = pendingTextRef.current;
        pendingTextRef.current = "";
        if (text) update((s) => ({ ...s, textBatches: [...s.textBatches, text] }));
      };

      setState({ isStreaming: true, steps: [], textBatches: [] });
      try {
        const proposal = await streamProposal(projectId, input, {
          signal: controller.signal,
          onProgress: (step) => update((s) => ({ ...s, steps: upsertStep(s.steps, step) })),
          onText: (delta) => {
            pendingTextRef.current += delta;
            flushTimerRef.current ??= setTimeout(flush, STREAM_TEXT_FLUSH_MS);
          },
        });
        if (!isCurrent()) return;
        queryClient.setQueryData(queryKeys.proposal(proposal.id), proposal);
        clearFlush();
        setState(IDLE_STREAM);
        callbacks.onSuccess(proposal);
      } catch (error) {
        const cancelled = controller.signal.aborted;
        if (controllerRef.current !== controller) {
          // Superseded by a newer request: that request owns the state now.
          if (cancelled) callbacks.onCancel?.();
          return;
        }
        clearFlush();
        setState(IDLE_STREAM);
        if (cancelled) callbacks.onCancel?.();
        else callbacks.onError(error);
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
      }
    },
    [clearFlush, projectId, queryClient],
  );

  return { ...state, start, abort };
}

export function useFixFinding(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (findingId: string) => fixFinding(projectId, findingId),
    onSuccess: (proposal) => queryClient.setQueryData(queryKeys.proposal(proposal.id), proposal),
  });
}

export function useApplyProposal(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ proposalId, baseVersion }: { proposalId: string; baseVersion: number }) =>
      applyProposal(proposalId, baseVersion),
    onSuccess: (architecture, { proposalId }) => {
      queryClient.setQueryData<Architecture | null>(queryKeys.architecture(projectId), architecture);
      invalidateArchitectureDependents(queryClient, projectId);
      track("architecture_change_applied", { projectId, proposalId, version: architecture.version });
    },
  });
}

export function useRejectProposal(projectId: string) {
  return useMutation({
    mutationFn: (proposalId: string) => rejectProposal(proposalId),
    onSuccess: (proposal) => {
      track("architecture_change_rejected", { projectId, proposalId: proposal.id });
    },
  });
}

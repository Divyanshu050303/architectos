import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTransport, type Transport, type TransportRequest } from "@/api/client";
import { CommandBar } from "@/features/architecture/components/CommandBar";
import { ProposalPanel } from "@/features/architecture/components/ProposalPanel";
import { useProposal } from "@/hooks/use-proposals";
import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { Proposal } from "@/types/architecture";

const proposal: Proposal = {
  id: "prop_9",
  projectId: "proj_1",
  prompt: "Add Redis caching.",
  baseVersion: 3,
  kind: "change",
  recommendation: "Introduce Redis.",
  reason: "Reads are high.",
  impact: [],
  cost: null,
  evidenceIds: [],
  changes: [{ op: "remove_node", nodeId: "legacy", name: "Legacy cache" }],
  validation: {
    summary: "Mock validation: no new findings.",
    newFindings: [],
    resolvedFindingIds: [],
    passes: true,
  },
  status: "pending",
};

/** A streaming transport whose NDJSON lines the test pushes one at a time. */
function controllableStream() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
    cancel,
  });
  const requests: TransportRequest[] = [];
  const transport: Transport = async (request) => {
    requests.push(request);
    return { status: 200, body: null, headers: { get: () => null }, stream };
  };
  return {
    transport,
    requests,
    cancel,
    push: (event: unknown) => act(() => controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`))),
  };
}

const step = (id: string, label: string, status: string) => ({
  type: "progress",
  step: { id, label, status },
});

function PanelHost() {
  const activeProposalId = useCommandStore((s) => s.activeProposalId);
  const active = useProposal(activeProposalId).data;
  return active ? (
    <ProposalPanel
      projectId="proj_1"
      proposal={active}
      isDirty={false}
      onApplied={() => undefined}
      onClose={() => useCommandStore.getState().clearProposal()}
    />
  ) : null;
}

function renderBar(props: { isDirty?: boolean; baseVersion?: number | null } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <PanelHost />
      <CommandBar projectId="proj_1" baseVersion={props.baseVersion ?? 3} isDirty={props.isDirty ?? false} />
    </QueryClientProvider>,
  );
  return screen.getByRole("textbox", { name: /Ask ArchitectOS/ });
}

beforeEach(() => {
  useCommandStore.getState().reset();
  useWorkspaceStore.getState().reset();
});

afterEach(() => setTransport(null));

describe("CommandBar", () => {
  it("streams with mod+enter, sending the base version and selection", async () => {
    const s = controllableStream();
    setTransport(s.transport);
    useWorkspaceStore.getState().select({ nodeIds: ["postgres"] });
    const input = renderBar();
    await userEvent.type(input, "Add Redis caching.");
    await userEvent.keyboard("{Control>}{Enter}{/Control}");

    await waitFor(() => expect(s.requests).toHaveLength(1));
    expect(new URL(s.requests[0]!.url).pathname).toMatch(/\/projects\/proj_1\/proposals\/stream$/);
    expect(JSON.parse(s.requests[0]!.body ?? "")).toEqual({
      prompt: "Add Redis caching.",
      baseVersion: 3,
      selectedNodeIds: ["postgres"],
    });
    expect(useCommandStore.getState().recentPrompts[0]).toBe("Add Redis caching.");
  });

  it("drives steps from stream events, shows batched text, then opens the proposal", async () => {
    const s = controllableStream();
    setTransport(s.transport);
    const input = renderBar();
    await userEvent.type(input, "Add Redis caching.");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText("Sending request")).toBeInTheDocument();

    await s.push(step("understand", "Understanding request", "pending"));
    await s.push(step("draft", "Drafting proposal", "pending"));
    await s.push(step("understand", "Understanding request", "running"));
    const status = await screen.findByRole("status");
    await waitFor(() => expect(within(status).getByText("Understanding request")).toBeInTheDocument());
    expect(status).toHaveTextContent("Understanding request: In progress");
    expect(status).toHaveTextContent("Drafting proposal: Pending");
    expect(screen.queryByText("Sending request")).not.toBeInTheDocument();

    await s.push(step("understand", "Understanding request", "done"));
    await s.push(step("draft", "Drafting proposal", "running"));
    await waitFor(() => expect(status).toHaveTextContent("Understanding request: Done"));
    expect(status).toHaveTextContent("Drafting proposal: In progress");

    await s.push({ type: "text", delta: "Introduce " });
    await s.push({ type: "text", delta: "Redis. " });
    await s.push({ type: "text", delta: "Reads are high." });
    const log = await screen.findByRole("log", { name: "AI explanation" }, { timeout: 2000 });
    expect(log).toHaveAttribute("aria-live", "polite");
    await waitFor(() => expect(log).toHaveTextContent("Introduce Redis. Reads are high."));
    // Deltas arriving together are announced as one batch, not per token.
    expect(log.querySelectorAll("span")).toHaveLength(1);
    expect(screen.queryByRole("article")).not.toBeInTheDocument();

    await s.push({ type: "proposal", proposal });
    const panel = await screen.findByRole("article", { name: "Architecture change proposal" });
    expect(within(panel).getByText("Introduce Redis.")).toBeInTheDocument();
    expect(useCommandStore.getState().activeProposalId).toBe("prop_9");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(s.cancel).toHaveBeenCalled(); // reading stops after the final proposal
  });

  it("aborts the stream with Stop, keeping the prompt and applying nothing", async () => {
    const s = controllableStream();
    setTransport(s.transport);
    const input = renderBar();
    await userEvent.type(input, "Add Redis caching.");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await s.push(step("understand", "Understanding request", "running"));
    await screen.findByText("Understanding request");

    await userEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(s.cancel).toHaveBeenCalled();
    expect(input).toHaveValue("Add Redis caching.");
    expect(useCommandStore.getState()).toMatchObject({ status: "idle", error: null, activeProposalId: null });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("aborts with Escape while streaming", async () => {
    const s = controllableStream();
    setTransport(s.transport);
    const input = renderBar();
    await userEvent.type(input, "Add Redis caching.");
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await screen.findByRole("status");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(s.cancel).toHaveBeenCalled();
  });

  it("shows in-stream errors with the request id and reassurance", async () => {
    const s = controllableStream();
    setTransport(s.transport);
    const input = renderBar();
    await userEvent.type(input, "Add Redis caching.");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await s.push({
      type: "error",
      code: "model_overloaded",
      message: "Try again soon.",
      requestId: "req_77",
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Try again soon.");
    expect(alert).toHaveTextContent("No changes were applied.");
    expect(screen.getByText("req_77")).toBeInTheDocument();
  });

  it("is disabled while the prompt is empty", async () => {
    const transport = vi.fn<Transport>();
    setTransport(transport);
    const input = renderBar();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await userEvent.click(input);
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(transport).not.toHaveBeenCalled();
  });

  it("is disabled with an explanation while the draft is dirty", async () => {
    const transport = vi.fn<Transport>();
    setTransport(transport);
    const input = renderBar({ isDirty: true });
    await userEvent.type(input, "Add Redis caching.");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(input).toHaveAccessibleDescription(/Save or discard your changes before asking for a proposal/);
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(transport).not.toHaveBeenCalled();
  });

  it("shows failures with the request id and reassurance", () => {
    useCommandStore.getState().submitFailed({
      title: "The proposal could not be created.",
      message: "Timed out.",
      requestId: "req_42",
    });
    renderBar();
    expect(screen.getByRole("alert")).toHaveTextContent("No changes were applied.");
    expect(screen.getByText("req_42")).toBeInTheDocument();
  });

  it("recalls recent prompts with ArrowUp when empty", async () => {
    useCommandStore.setState({ recentPrompts: ["What breaks at 10M users?", "Add Redis caching."] });
    const input = renderBar();
    await userEvent.click(input);
    await userEvent.keyboard("{ArrowUp}");
    expect(input).toHaveValue("What breaks at 10M users?");
    await userEvent.keyboard("{ArrowUp}");
    expect(input).toHaveValue("Add Redis caching.");
    await userEvent.keyboard("{ArrowDown}");
    expect(input).toHaveValue("What breaks at 10M users?");
  });

  it("Edit on the proposal puts the prompt back, focused, for resubmission", async () => {
    const s = controllableStream();
    setTransport(s.transport);
    const input = renderBar();
    await userEvent.type(input, "Add Redis caching.");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await s.push({ type: "proposal", proposal });
    const panel = await screen.findByRole("article");
    expect(input).toHaveValue("");

    await userEvent.click(within(panel).getByRole("button", { name: "Edit" }));

    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(input).toHaveValue("Add Redis caching.");
    await waitFor(() => expect(input).toHaveFocus());
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
  });
});

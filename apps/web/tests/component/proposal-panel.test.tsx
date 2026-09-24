import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProposalPanel } from "@/features/architecture/components/ProposalPanel";
import { useCommandStore } from "@/stores/command-store";
import { useUiStore } from "@/stores/ui-store";
import type { Architecture, Proposal } from "@/types/architecture";

const mocks = vi.hoisted(() => ({ apply: vi.fn(), reject: vi.fn() }));

vi.mock("@/hooks/use-proposals", () => ({
  useApplyProposal: () => ({ mutate: mocks.apply, isPending: false, error: null }),
  useRejectProposal: () => ({ mutate: mocks.reject, isPending: false, error: null }),
}));

const proposal: Proposal = {
  id: "prop_1",
  projectId: "proj_1",
  prompt: "Add Redis caching.",
  baseVersion: 3,
  kind: "change",
  recommendation: "Introduce Redis.",
  reason: "PostgreSQL read utilization exceeds the configured threshold.",
  impact: [{ metric: "DB reads", before: 18000, after: 6000, unit: "/sec", evidenceId: "ev_reads" }],
  cost: { before: 210, after: 247, currency: "USD", period: "month" },
  evidenceIds: ["ev_reads", "ev_redis"],
  changes: [
    {
      op: "add_node",
      node: {
        id: "redis",
        type: "cache",
        name: "Redis",
        technology: "Redis 7",
        configuration: {},
        position: { x: 0, y: 0 },
      },
    },
    {
      op: "add_edge",
      edge: { id: "e_api_redis", source: "api", target: "redis", synchronous: true, critical: true },
      sourceName: "API",
      targetName: "Redis",
    },
    { op: "update_node", nodeId: "postgres", name: "PostgreSQL", field: "replicas", before: 1, after: 2 },
    { op: "remove_node", nodeId: "legacy", name: "Legacy cache" },
  ],
  validation: {
    summary: "No new findings, 1 resolved.",
    newFindings: [],
    resolvedFindingIds: ["f_db_reads"],
    passes: true,
  },
  status: "pending",
};

const criticalValidation: Proposal["validation"] = {
  summary: "2 new finding(s).",
  newFindings: [
    { severity: "critical", title: "Redis is a single point of failure", location: "Redis" },
    { severity: "low", title: "Missing cache metrics", location: "Redis" },
  ],
  resolvedFindingIds: [],
  passes: false,
};

function renderPanel(overrides: Partial<Proposal> = {}, props: { isDirty?: boolean } = {}) {
  const onApplied = vi.fn();
  const onClose = vi.fn(() => useCommandStore.getState().clearProposal());
  render(
    <ProposalPanel
      projectId="proj_1"
      proposal={{ ...proposal, ...overrides }}
      isDirty={props.isDirty ?? false}
      onApplied={onApplied}
      onClose={onClose}
    />,
  );
  return { onApplied, onClose };
}

beforeEach(() => {
  mocks.apply.mockReset();
  mocks.reject.mockReset();
  useUiStore.getState().reset();
  useCommandStore.getState().reset();
});

describe("ProposalPanel", () => {
  it("renders the structured proposal and every diff line", () => {
    renderPanel();
    expect(screen.getByText("Introduce Redis.")).toBeInTheDocument();
    expect(screen.getByText("AI proposal")).toBeInTheDocument();

    const changes = within(screen.getByRole("list", { name: "Proposed changes" })).getAllByRole("listitem");
    expect(changes.map((li) => li.textContent)).toEqual([
      "+Add: Redis · Redis 7",
      "+Add: API → Redis",
      "~Change: PostgreSQL · replicas 1 → 2",
      "−Remove: Legacy cache",
    ]);

    expect(screen.getByText(/18K → 6K/)).toBeInTheDocument();
    expect(screen.getByText(/\$210 → \$247/)).toBeInTheDocument();
  });

  it("opens evidence from the impact Why? button", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Why? Evidence for DB reads" }));
    expect(useUiStore.getState().evidenceId).toBe("ev_reads");
  });

  it("applies against the proposal's base version", async () => {
    const { onApplied } = renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(mocks.apply).toHaveBeenCalledWith({ proposalId: "prop_1", baseVersion: 3 }, expect.any(Object));

    const options = mocks.apply.mock.calls[0]?.[1] as { onSuccess: (a: Architecture) => void };
    const applied = { version: 4 } as Architecture;
    options.onSuccess(applied);
    expect(onApplied).toHaveBeenCalledWith(applied);
  });

  it("rejects and closes", async () => {
    const { onClose } = renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(mocks.reject).toHaveBeenCalledWith("prop_1", expect.any(Object));
    const options = mocks.reject.mock.calls[0]?.[1] as { onSuccess: () => void };
    options.onSuccess();
    expect(onClose).toHaveBeenCalled();
  });

  it("blocks Apply while the draft has unsaved changes", () => {
    renderPanel({}, { isDirty: true });
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(screen.getByText(/Save or discard your unsaved changes/)).toBeInTheDocument();
  });

  it("shows only recommendation, reason and evidence for answers", () => {
    renderPanel({ kind: "answer", changes: [], impact: [], cost: null });
    expect(screen.getByText("Introduce Redis.")).toBeInTheDocument();
    expect(screen.getByText("AI answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Proposed changes" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ev_redis" })).toBeInTheDocument();
  });

  it("offers Apply, Edit and Reject for changes", () => {
    renderPanel();
    const footer = screen.getByRole("button", { name: "Apply" }).parentElement;
    expect(footer).not.toBeNull();
    const labels = within(footer as HTMLElement)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(labels).toEqual(["Reject", "Edit", "Apply"]);
  });

  it("Edit restores the original prompt to the command bar and closes without applying", async () => {
    const input = document.createElement("textarea");
    input.id = "architecture-command-input";
    document.body.append(input);
    useCommandStore.setState({ activeProposalId: "prop_1", prompt: "" });
    const { onClose } = renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(onClose).toHaveBeenCalled();
    expect(useCommandStore.getState().prompt).toBe("Add Redis caching.");
    expect(useCommandStore.getState().activeProposalId).toBeNull();
    expect(mocks.apply).not.toHaveBeenCalled();
    expect(mocks.reject).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(document.activeElement).toBe(input));
    input.remove();
  });

  it("shows a passing validation with resolved findings before Apply", () => {
    renderPanel();
    const section = screen.getByRole("region", { name: "Validation" });
    expect(within(section).getByText("Passes validation")).toBeInTheDocument();
    expect(within(section).getByText("No new findings, 1 resolved.")).toBeInTheDocument();
    expect(within(section).getByText(/Resolves 1 finding/)).toBeInTheDocument();
    expect(within(section).getByText("f_db_reads")).toBeInTheDocument();
    // The validation section precedes the actions.
    expect(
      section.compareDocumentPosition(screen.getByRole("button", { name: "Apply" })) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("lists new findings with severity text, not colour alone", () => {
    renderPanel({ validation: criticalValidation });
    const section = screen.getByRole("region", { name: "Validation" });
    expect(within(section).getByText("2 new findings")).toBeInTheDocument();
    const items = within(screen.getByRole("list", { name: "New findings" })).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "CriticalRedis is a single point of failure · Redis",
      "LowMissing cache metrics · Redis",
    ]);
  });

  it("says when a change was not validated", () => {
    renderPanel({ validation: null });
    expect(screen.getByText(/This proposal was not validated/)).toBeInTheDocument();
  });

  it("applies directly when validation passes", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.apply).toHaveBeenCalledTimes(1);
  });

  it("asks for confirmation before applying new critical findings", async () => {
    renderPanel({ validation: criticalValidation });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    const dialog = await screen.findByRole("dialog", { name: "Apply with new findings?" });
    expect(mocks.apply).not.toHaveBeenCalled();
    expect(dialog).toHaveTextContent("Redis is a single point of failure");
    expect(dialog).not.toHaveTextContent("Missing cache metrics");

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(mocks.apply).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Apply anyway" }),
    );
    expect(mocks.apply).toHaveBeenCalledWith({ proposalId: "prop_1", baseVersion: 3 }, expect.any(Object));
  });

  it("does not confirm for low/medium findings only", async () => {
    renderPanel({
      validation: { ...criticalValidation, newFindings: [criticalValidation!.newFindings[1]!] },
    });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.apply).toHaveBeenCalledTimes(1);
  });

  it("animates diff lines in with the motion-appear utility", () => {
    renderPanel();
    const items = within(screen.getByRole("list", { name: "Proposed changes" })).getAllByRole("listitem");
    expect(items.every((li) => li.classList.contains("motion-appear"))).toBe(true);
    expect(items[2]?.style.getPropertyValue("--motion-index")).toBe("2");
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

import { setTransport } from "@/api/client";
import { resetMockDb } from "@/api/mock/db";
import { mockTransport } from "@/api/mock/transport";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ArchitectureWorkspace } from "@/features/architecture/components/ArchitectureWorkspace";
import type * as ToolbarModule from "@/features/architecture/components/ArchitectureToolbar";
import type * as CommandBarModule from "@/features/architecture/components/CommandBar";
import type * as ProposalPanelModule from "@/features/architecture/components/ProposalPanel";
import type * as OverlayChromeModule from "@/features/architecture/components/OverlayChrome";
import { useArchitectureStore } from "@/stores/architecture-store";
import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { Proposal } from "@/types/architecture";

const failing = vi.hoisted(() => ({ regions: new Set<string>() }));

function boom(region: string): void {
  if (failing.regions.has(region)) throw new Error(`${region} exploded`);
}

vi.mock("next/navigation", () => ({
  useParams: () => ({ projectId: "proj_food" }),
  usePathname: () => "/project/proj_food/architecture",
  useRouter: () => ({ push: () => {}, replace: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));

// React Flow needs real layout; a stub keeps the test about the boundaries.
vi.mock("@/features/architecture/components/ArchitectureCanvas", () => ({
  ArchitectureCanvas: ({ nodes }: { nodes: readonly unknown[] }) => (
    <div role="application" aria-label="Architecture canvas">
      {nodes.length} components
    </div>
  ),
}));

vi.mock("@/features/architecture/components/ArchitectureToolbar", async (importOriginal) => {
  const actual = await importOriginal<typeof ToolbarModule>();
  return {
    ...actual,
    ArchitectureToolbar: (props: Parameters<typeof actual.ArchitectureToolbar>[0]) => {
      boom("toolbar");
      return <actual.ArchitectureToolbar {...props} />;
    },
  };
});

vi.mock("@/features/architecture/components/CommandBar", async (importOriginal) => {
  const actual = await importOriginal<typeof CommandBarModule>();
  return {
    ...actual,
    CommandBar: (props: Parameters<typeof actual.CommandBar>[0]) => {
      boom("command bar");
      return <actual.CommandBar {...props} />;
    },
  };
});

vi.mock("@/features/architecture/components/ProposalPanel", async (importOriginal) => {
  const actual = await importOriginal<typeof ProposalPanelModule>();
  return {
    ...actual,
    ProposalPanel: (props: Parameters<typeof actual.ProposalPanel>[0]) => {
      boom("proposal");
      return <actual.ProposalPanel {...props} />;
    },
  };
});

vi.mock("@/features/architecture/components/OverlayChrome", async (importOriginal) => {
  const actual = await importOriginal<typeof OverlayChromeModule>();
  return {
    ...actual,
    OverlayLegend: (props: Parameters<typeof actual.OverlayLegend>[0]) => {
      boom("legend");
      return <actual.OverlayLegend {...props} />;
    },
  };
});

const proposal: Proposal = {
  id: "prop_boom",
  projectId: "proj_food",
  prompt: "Add a cache.",
  baseVersion: 3,
  kind: "change",
  recommendation: "Add Redis.",
  reason: "Reads are high.",
  impact: [],
  cost: null,
  evidenceIds: [],
  changes: [],
  status: "pending",
  validation: null,
} as unknown as Proposal;

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  client.setQueryData(["proposal", proposal.id], proposal);
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ArchitectureWorkspace />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("workspace error boundaries (spec §82)", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    failing.regions.clear();
    resetMockDb();
    setTransport(mockTransport);
    useArchitectureStore.getState().reset();
    useWorkspaceStore.getState().reset();
    useCommandStore.getState().reset();
  });

  afterAll(() => setTransport(null));

  it("keeps the canvas when the toolbar and command bar fail", async () => {
    failing.regions.add("toolbar");
    failing.regions.add("command bar");
    renderWorkspace();

    expect(await screen.findByRole("application", { name: "Architecture canvas" })).toBeInTheDocument();
    expect(screen.getByText("Toolbar could not be displayed.")).toBeInTheDocument();
    expect(screen.getByText("Command bar could not be displayed.")).toBeInTheDocument();
  });

  it("keeps the canvas when the overlay chrome fails", async () => {
    failing.regions.add("legend");
    renderWorkspace();

    expect(await screen.findByRole("application", { name: "Architecture canvas" })).toBeInTheDocument();
    expect(screen.getByText("Legend could not be displayed.")).toBeInTheDocument();
    // The rest of the chrome still renders.
    expect(screen.getByRole("radiogroup", { name: "Graph level" })).toBeInTheDocument();
  });

  it("keeps the canvas when the proposal panel fails", async () => {
    failing.regions.add("proposal");
    useCommandStore.setState({ activeProposalId: proposal.id });
    renderWorkspace();

    expect(await screen.findByRole("application", { name: "Architecture canvas" })).toBeInTheDocument();
    expect(await screen.findByText("Proposal could not be displayed.")).toBeInTheDocument();
  });
});

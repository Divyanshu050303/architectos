import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTransport, type Transport, type TransportRequest } from "@/api/client";
import { resetMockDb } from "@/api/mock/db";
import { mockTransport } from "@/api/mock/transport";
import { DiscoveryView } from "@/features/discovery/components/DiscoveryView";

const nav = vi.hoisted(() => ({
  search: "",
  listeners: new Set<() => void>(),
}));

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (listener: () => void) => {
    nav.listeners.add(listener);
    return () => nav.listeners.delete(listener);
  };
  return {
    usePathname: () => "/project/proj_food/infrastructure",
    useRouter: () => ({
      push: () => {},
      replace: (url: string) => {
        nav.search = url.split("?")[1] ?? "";
        for (const listener of nav.listeners) listener();
      },
    }),
    useSearchParams: () => new URLSearchParams(useSyncExternalStore(subscribe, () => nav.search)),
  };
});

const requests: TransportRequest[] = [];
const recordingTransport: Transport = (request) => {
  requests.push(request);
  return mockTransport(request);
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DiscoveryView projectId="proj_food" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  resetMockDb();
  requests.length = 0;
  nav.search = "";
  setTransport(recordingTransport);
  // Only Date is faked: the mock backend derives run progress from Date.now().
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-20T10:00:00.000Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

afterAll(() => setTransport(null));

describe("DiscoveryView", () => {
  it("starts a scan, renders run steps and saves with the reviewed base version", async () => {
    const user = userEvent.setup();
    renderView();

    const aws = await screen.findByRole("region", { name: "AWS" });
    expect(within(aws).getByText("Connected")).toBeInTheDocument();
    await user.type(within(aws).getByLabelText("Region"), "us-east-1");
    await user.click(within(aws).getByRole("button", { name: "Start AWS scan" }));

    // The run id lands in the URL (?run=) and the stepper follows the run's steps.
    await waitFor(() => expect(nav.search).toMatch(/^run=disc_/));
    const start = requests.find(
      (r) => r.method === "POST" && r.url.endsWith("/projects/proj_food/discoveries"),
    );
    expect(JSON.parse(start?.body ?? "{}")).toEqual({ connector: "aws", options: { region: "us-east-1" } });

    const stepper = screen.getByRole("navigation", { name: "Discovery progress" });
    expect(within(stepper).getByText(/Connect/)).toBeInTheDocument();
    expect(within(stepper).getByText(/Generate architecture/)).toBeInTheDocument();
    expect(within(stepper).getByText("Save")).toBeInTheDocument();

    // Let the backend run finish; the next poll picks it up.
    vi.setSystemTime(new Date("2026-09-20T10:00:10.000Z"));
    await screen.findByRole("region", { name: "Proposed architecture" }, { timeout: 3000 });
    expect(within(stepper).getByText("183 resources")).toBeInTheDocument();
    expect(within(stepper).getByText("Awaiting approval")).toBeInTheDocument();
    expect(screen.getByText("Showing 183 of 183 resources")).toBeInTheDocument();

    // Status filter narrows the resource table.
    await user.click(screen.getByRole("button", { name: "Show unmapped" }));
    expect(screen.getByRole("button", { name: /^Unmapped/, pressed: true })).toBeInTheDocument();

    // Nothing is saved until the user approves.
    expect(requests.some((r) => r.url.includes("/save"))).toBe(false);
    const saveButton = screen.getByRole("button", { name: /^Save as v4$/ });
    await user.click(saveButton);

    await screen.findByText("Saved as v4", { selector: "p" });
    const save = requests.find((r) => r.method === "POST" && r.url.endsWith("/save"));
    expect(JSON.parse(save?.body ?? "{}")).toEqual({ baseVersion: 3 });
    expect(screen.getByRole("link", { name: /Open architecture v4/ })).toHaveAttribute(
      "href",
      "/project/proj_food/architecture",
    );
  });

  it("explains how to fix a Terraform scan that cannot start (422)", async () => {
    const user = userEvent.setup();
    renderView();

    const terraform = await screen.findByRole("region", { name: "Terraform" });
    expect(within(terraform).getByText("Not connected")).toBeInTheDocument();
    await user.click(within(terraform).getByRole("button", { name: "Start Terraform scan" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Terraform scan could not start.");
    expect(alert).toHaveTextContent("Connect terraform before running discovery.");
    expect(alert).toHaveTextContent("Add read-only Terraform credentials in project settings");
    expect(alert).toHaveTextContent("No changes were applied.");
    expect(alert).toHaveTextContent(/Request ID:/);
    expect(nav.search).toBe("");
  });
});

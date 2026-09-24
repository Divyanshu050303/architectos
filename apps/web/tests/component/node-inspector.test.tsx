import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { NodeInspector, type NodeInspectorProps } from "@/features/architecture/components/NodeInspector";
import type { ArchitectureNode } from "@/types/architecture";
import type { ComponentUtilization } from "@/types/capacity";
import type { Finding } from "@/types/validation";

const postgres: ArchitectureNode = {
  id: "postgres",
  type: "database",
  name: "PostgreSQL",
  technology: "PostgreSQL 16",
  description: "Primary store",
  configuration: { replicas: 2, cpu: "4 vCPU" },
  position: { x: 0, y: 0 },
};

const api: ArchitectureNode = {
  id: "api",
  type: "service",
  name: "API",
  technology: "Node.js",
  configuration: {},
  position: { x: 0, y: 0 },
};

const readsRow: ComponentUtilization = {
  nodeId: "postgres",
  resource: "Reads",
  used: 8200,
  limit: 12000,
  unit: "/s",
  utilization: 0.68,
  threshold: 0.7,
  status: "healthy",
  evidenceId: "ev_reads",
};

const spof: Finding = {
  id: "f1",
  ruleId: "reliability.single_point_of_failure",
  category: "reliability",
  severity: "critical",
  title: "PostgreSQL is a single point of failure",
  location: "PostgreSQL",
  whyItMatters: "One instance failing takes the API down.",
  recommendation: "Add a replica.",
  nodeIds: ["postgres"],
  edgeIds: [],
  evidenceIds: ["ev_spof"],
  fixable: true,
  status: "open",
};

function Harness(props: Partial<NodeInspectorProps>) {
  const [tab, setTab] = useState("overview");
  return (
    <NodeInspector
      projectId="proj_1"
      node={postgres}
      architecture={{
        nodes: [postgres, api],
        edges: [{ id: "e1", source: "api", target: "postgres", synchronous: true, critical: true }],
      }}
      utilization={[]}
      findings={[]}
      status="healthy"
      statusLabel="Healthy"
      tab={tab}
      onTabChange={setTab}
      editable
      onCommand={vi.fn()}
      onOpenEvidence={vi.fn()}
      onSelectNode={vi.fn()}
      {...props}
    />
  );
}

const tabNames = () => screen.getAllByRole("tab").map((tab) => tab.textContent);

describe("NodeInspector", () => {
  it("shows only tabs that have data", () => {
    render(<Harness />);
    expect(tabNames()).toEqual(["Overview", "Configuration"]);
    expect(screen.getByRole("heading", { name: "PostgreSQL" })).toBeInTheDocument();
    // Neighbours come from the graph.
    expect(screen.getByRole("button", { name: "API" })).toBeInTheDocument();
  });

  it("adds capacity, constraints, failure modes and evidence when data exists", async () => {
    const onOpenEvidence = vi.fn();
    render(<Harness utilization={[readsRow]} findings={[spof]} onOpenEvidence={onOpenEvidence} />);
    expect(tabNames()).toEqual([
      "Overview",
      "Configuration",
      "Capacity",
      "Constraints",
      "Failure modes",
      "Evidence",
    ]);

    await userEvent.click(screen.getByRole("tab", { name: "Capacity" }));
    expect(screen.getByText("8.2K / 12K")).toBeInTheDocument();
    expect(screen.getByText("Calculated")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Why? Evidence for Reads" }));
    expect(onOpenEvidence).toHaveBeenCalledWith("ev_reads");

    await userEvent.click(screen.getByRole("tab", { name: "Failure modes" }));
    expect(screen.getByText("PostgreSQL is a single point of failure")).toBeInTheDocument();
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });

  it("dispatches CHANGE_REPLICAS when replicas are edited", async () => {
    const onCommand = vi.fn();
    render(<Harness onCommand={onCommand} />);
    await userEvent.click(screen.getByRole("tab", { name: "Configuration" }));

    const replicas = screen.getByRole("spinbutton", { name: "Replicas" });
    await userEvent.clear(replicas);
    await userEvent.type(replicas, "3{Enter}");

    expect(onCommand).toHaveBeenCalledWith({ type: "CHANGE_REPLICAS", nodeId: "postgres", replicas: 3 });
  });

  it("dispatches UPDATE_CONFIGURATION for other scalar values", async () => {
    const onCommand = vi.fn();
    render(<Harness onCommand={onCommand} />);
    await userEvent.click(screen.getByRole("tab", { name: "Configuration" }));

    const cpu = screen.getByRole("textbox", { name: "Cpu" });
    await userEvent.clear(cpu);
    await userEvent.type(cpu, "8 vCPU{Enter}");

    expect(onCommand).toHaveBeenCalledWith({
      type: "UPDATE_CONFIGURATION",
      nodeId: "postgres",
      configuration: { cpu: "8 vCPU" },
    });
  });

  it("renames inline", async () => {
    const onCommand = vi.fn();
    render(<Harness onCommand={onCommand} />);
    await userEvent.click(screen.getByRole("button", { name: "Rename PostgreSQL" }));
    const input = screen.getByRole("textbox", { name: "Component name" });
    await userEvent.clear(input);
    await userEvent.type(input, "Orders DB{Enter}");
    expect(onCommand).toHaveBeenCalledWith({
      type: "RENAME_COMPONENT",
      nodeId: "postgres",
      name: "Orders DB",
    });
  });

  it("is read-only when editing is not allowed", async () => {
    render(<Harness editable={false} />);
    expect(screen.queryByRole("button", { name: "Rename PostgreSQL" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Configuration" }));
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("adds Security, Observability and Cost tabs only when the analyses cover the component", async () => {
    const onOpenEvidence = vi.fn();
    render(
      <Harness
        onOpenEvidence={onOpenEvidence}
        security={{
          exposure: { nodeId: "postgres", level: "private", reason: "Private subnet, no public endpoint." },
          controls: {
            nodeId: "postgres",
            authentication: true,
            authorization: null,
            encryptionInTransit: true,
            encryptionAtRest: false,
            secretsManagement: true,
            handlesPii: true,
          },
          threats: [
            {
              id: "t1",
              title: "Unencrypted backups",
              category: "information_disclosure",
              severity: "high",
              nodeIds: ["postgres"],
              mitigation: "Enable storage encryption.",
              evidenceId: "ev_threat",
            },
          ],
        }}
        observability={{
          coverage: {
            nodeId: "postgres",
            metrics: true,
            logs: true,
            traces: false,
            alerts: true,
            dashboards: false,
          },
          slos: [
            {
              id: "slo1",
              name: "Checkout success rate",
              nodeIds: ["postgres"],
              target: 0.999,
              current: 0.9985,
              errorBudgetRemaining: 0.2,
              window: "30d",
            },
          ],
          gap: { nodeId: "postgres", missing: ["traces", "dashboards"], recommendation: "Add tracing." },
        }}
        cost={{
          cost: {
            nodeId: "postgres",
            monthly: 184,
            breakdown: [
              { item: "db.m6g.xlarge instance", monthly: 138 },
              { item: "500 GB gp3 storage", monthly: 46 },
            ],
          },
          currency: "USD",
          evidenceId: "ev_cost",
        }}
      />,
    );
    expect(tabNames()).toEqual([
      "Overview",
      "Configuration",
      "Security",
      "Observability",
      "Cost",
      "Evidence",
    ]);

    await userEvent.click(screen.getByRole("tab", { name: "Security" }));
    expect(screen.getByText("Private")).toBeInTheDocument();
    expect(screen.getByText("Unencrypted backups")).toBeInTheDocument();
    expect(screen.getByText("Information disclosure")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Observability" }));
    expect(screen.getByText("Checkout success rate")).toBeInTheDocument();
    expect(screen.getAllByText("Missing", { selector: "dd span" })).toHaveLength(2);
    expect(screen.getByText("Add tracing.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Cost" }));
    expect(screen.getByText("$184/mo")).toBeInTheDocument();
    expect(screen.getByText("$138")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Why? Evidence for cost estimate" }));
    expect(onOpenEvidence).toHaveBeenCalledWith("ev_cost");
  });

  it("hides analysis tabs without data for the component", () => {
    render(
      <Harness security={{ exposure: null, controls: null, threats: [] }} observability={null} cost={null} />,
    );
    expect(tabNames()).toEqual(["Overview", "Configuration"]);
  });
});

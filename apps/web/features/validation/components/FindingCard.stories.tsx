import type { Decorator, Meta, StoryObj } from "@storybook/nextjs-vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { queryKeys } from "@/lib/query-keys";
import type { Architecture } from "@/types/architecture";
import type { Finding } from "@/types/validation";

import { FindingCard } from "./FindingCard";

const PROJECT_ID = "proj_food";

/** Seeded so "Create ADR" can resolve component names without a network request. */
const ARCHITECTURE: Architecture = {
  id: "arch_food_v3",
  projectId: PROJECT_ID,
  version: 3,
  nodes: [
    {
      id: "api",
      type: "service",
      name: "API",
      technology: "Node.js",
      configuration: { replicas: 3 },
      position: { x: 0, y: 0 },
    },
    {
      id: "payment_service",
      type: "external",
      name: "Payment Service",
      technology: "Third-party API",
      configuration: {},
      position: { x: 300, y: 0 },
    },
    {
      id: "postgres",
      type: "database",
      name: "PostgreSQL",
      technology: "PostgreSQL 16",
      configuration: { replicas: 1 },
      position: { x: 300, y: 160 },
    },
  ],
  edges: [
    { id: "e_api_payment", source: "api", target: "payment_service", synchronous: true, critical: true },
    { id: "e_api_pg", source: "api", target: "postgres", synchronous: true, critical: true },
  ],
  assumptions: [],
  createdAt: "2026-09-20T09:30:00.000Z",
  createdBy: "user",
};

const TIMEOUT: Finding = {
  id: "f_timeout",
  ruleId: "reliability.missing_timeout",
  category: "reliability",
  severity: "high",
  title: "Missing timeout",
  location: "API → Payment Service",
  whyItMatters:
    "An external dependency without a timeout can block request workers until the pool is exhausted.",
  recommendation: "Add a timeout at or below the configured SLA (800 ms) and a circuit breaker.",
  nodeIds: ["api", "payment_service"],
  edgeIds: ["e_api_payment"],
  evidenceIds: ["ev_api_payment_timeout"],
  fixable: true,
  status: "open",
};

function StoryQueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: Number.POSITIVE_INFINITY, refetchOnWindowFocus: false },
        mutations: { retry: false },
      },
    });
    qc.setQueryData(queryKeys.architecture(PROJECT_ID), ARCHITECTURE);
    qc.setQueryData(queryKeys.decisions(PROJECT_ID), []);
    return qc;
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const withQueryClient: Decorator = (Story) => (
  <StoryQueryProvider>
    <div className="max-w-2xl">
      <Story />
    </div>
  </StoryQueryProvider>
);

const meta: Meta<typeof FindingCard> = {
  title: "Validation/FindingCard",
  component: FindingCard,
  decorators: [withQueryClient],
  parameters: {
    nextjs: { appDirectory: true, navigation: { pathname: `/project/${PROJECT_ID}/validation` } },
  },
  args: { projectId: PROJECT_ID, finding: TIMEOUT },
};
export default meta;

type Story = StoryObj<typeof FindingCard>;

export const High: Story = {};

export const Critical: Story = {
  args: {
    finding: {
      ...TIMEOUT,
      id: "f_pg_spof",
      ruleId: "reliability.single_point_of_failure",
      severity: "critical",
      title: "PostgreSQL is a single point of failure",
      location: "PostgreSQL",
      whyItMatters: "One instance failing takes every write path down.",
      recommendation: "Add a synchronous standby in a second availability zone.",
      nodeIds: ["postgres"],
      edgeIds: [],
      evidenceIds: ["ev_pg_spof"],
    },
  },
};

export const MediumNotFixable: Story = {
  args: {
    finding: {
      ...TIMEOUT,
      id: "f_logs",
      ruleId: "observability.missing_structured_logs",
      category: "observability",
      severity: "medium",
      title: "No structured logs on the API",
      location: "API",
      whyItMatters: "Incidents cannot be correlated across services without request IDs.",
      recommendation: "Emit JSON logs with a propagated request ID.",
      nodeIds: ["api"],
      edgeIds: [],
      fixable: false,
    },
  },
};

export const WithoutEvidenceOrLocation: Story = {
  name: "No evidence, no component",
  args: {
    finding: {
      ...TIMEOUT,
      id: "f_cost",
      ruleId: "cost.no_budget",
      category: "cost",
      severity: "low",
      title: "No monthly budget set",
      location: "Project",
      whyItMatters: "Cost regressions are only noticed on the invoice.",
      recommendation: "Set a monthly budget in the requirements.",
      nodeIds: [],
      edgeIds: [],
      evidenceIds: [],
      fixable: false,
    },
  },
};

export const Ignored: Story = { args: { finding: { ...TIMEOUT, status: "ignored" } } };

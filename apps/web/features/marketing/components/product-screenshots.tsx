/** Product "screenshots" composed from the real UI components, and the node-state gallery. */
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Meter } from "@/components/ui/progress";
import { ArchitectureNodeCard } from "@/features/architecture/components/ArchitectureNode";
import { MetricCard } from "@/features/capacity/components/MetricCard";
import { SeverityBadge } from "@/features/validation/severity";

import { marketingNode } from "../node-data";
import { MockFrame, Section, SectionHeader } from "./Section";

// --- Product screenshots ----------------------------------------------------

const CANVAS_NODES = {
  gateway: marketingNode({
    id: "gw",
    type: "gateway",
    name: "API Gateway",
    technology: "Envoy",
    status: "healthy",
    metric: { label: "CPU", value: "38%" },
    utilization: 0.38,
    capacity: true,
  }),
  orders: marketingNode({
    id: "orders",
    type: "service",
    name: "Order Service",
    technology: "Go",
    description: "×4",
    status: "healthy",
    metric: { label: "CPU", value: "61%" },
    utilization: 0.61,
    capacity: true,
  }),
  postgres: marketingNode({
    id: "pg",
    type: "database",
    name: "PostgreSQL",
    technology: "PostgreSQL 16",
    status: "warning",
    metric: { label: "connections", value: "82%" },
    utilization: 0.82,
    badges: [{ label: "Bottleneck", tone: "warning" }],
    capacity: true,
  }),
  kafka: marketingNode({
    id: "kafka",
    type: "queue",
    name: "Kafka",
    technology: "12 partitions",
    status: "healthy",
    metric: { label: "throughput", value: "31%" },
    utilization: 0.31,
    capacity: true,
  }),
};

function WorkspaceFrame() {
  return (
    <MockFrame
      title="Architecture workspace in capacity mode: the canvas is the product."
      path="food-delivery / architecture ?mode=capacity"
      className="lg:col-span-2"
    >
      <div className="grid md:grid-cols-[minmax(0,1fr)_16rem]">
        <div
          className="grid gap-4 bg-background p-4 sm:grid-cols-2 sm:p-6 [&_[data-status]]:w-full"
          style={{
            backgroundImage: "radial-gradient(var(--canvas-grid) 1px, transparent 1px)",
            backgroundSize: "16px 16px",
          }}
        >
          <ArchitectureNodeCard data={CANVAS_NODES.gateway} />
          <ArchitectureNodeCard data={CANVAS_NODES.orders} />
          <ArchitectureNodeCard data={CANVAS_NODES.postgres} selected />
          <ArchitectureNodeCard data={CANVAS_NODES.kafka} />
        </div>
        <aside
          aria-label="Inspector preview"
          className="hidden flex-col gap-3 border-l border-default p-4 md:flex"
        >
          <p className="label-caps">Database</p>
          <p className="-mt-2 text-base font-semibold text-fg">PostgreSQL</p>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted">Capacity</span>
            <ProvenanceTag kind="calculated" />
          </div>
          {[
            { resource: "Connections", value: 0.82, used: "410 / 500", tone: "warning" as const },
            { resource: "Writes", value: 0.54, used: "4.3K / 8K /s", tone: "accent" as const },
            { resource: "Storage", value: 0.27, used: "540 / 2K GB", tone: "accent" as const },
          ].map((row) => (
            <div key={row.resource} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between text-xs">
                <span className="text-fg">{row.resource}</span>
                <span className="tabular text-fg-secondary">{row.used}</span>
              </div>
              <Meter
                value={row.value}
                threshold={0.7}
                tone={row.tone}
                label={`${row.resource} utilization`}
              />
            </div>
          ))}
          <p className="mt-1 text-xs text-fg-secondary">
            Next limit: connections at <span className="tabular text-fg">~2.9M DAU</span>.
          </p>
        </aside>
      </div>
    </MockFrame>
  );
}

function ValidationFrame() {
  const groups = [
    { severity: "critical" as const, count: 1, title: "PostgreSQL is a single point of failure" },
    { severity: "high" as const, count: 2, title: "Missing timeout: API → Payment Service" },
    { severity: "medium" as const, count: 4, title: "No dead-letter queue on order events" },
  ];
  return (
    <MockFrame
      title="Validation: findings grouped by severity, each with evidence."
      path="food-delivery / validation"
    >
      <ul className="flex flex-col divide-y divide-default">
        {groups.map((group) => (
          <li key={group.severity} className="flex flex-col gap-1.5 px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <SeverityBadge severity={group.severity} />
              <span className="tabular text-2xs text-muted">
                {group.count} {group.count === 1 ? "finding" : "findings"}
              </span>
            </div>
            <p className="text-sm font-medium text-fg">{group.title}</p>
          </li>
        ))}
      </ul>
    </MockFrame>
  );
}

function CapacityFrame() {
  return (
    <MockFrame title="Capacity: current load and the next bottleneck." path="food-delivery / capacity">
      <div className="flex flex-col gap-3 p-4">
        <dl className="grid grid-cols-2 gap-2">
          <MetricCard label="DAU" value="2.4M" />
          <MetricCard label="Peak RPS" value="31K" />
        </dl>
        <div className="flex flex-col gap-1 rounded-md border border-warning/50 bg-warning-soft px-3 py-2">
          <span className="label-caps">Next bottleneck</span>
          <span className="text-sm font-medium text-fg">PostgreSQL connections</span>
          <span className="tabular text-xs text-fg-secondary">Expected at ~2.9M DAU</span>
        </div>
      </div>
    </MockFrame>
  );
}

export function ScreenshotsSection() {
  return (
    <Section labelledBy="screens-title" tone="muted">
      <SectionHeader
        id="screens-title"
        eyebrow="Product"
        title="Dense, calm and built for engineers."
        description="Rendered from the same components as the application, in the theme you are using now."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <WorkspaceFrame />
        <ValidationFrame />
        <CapacityFrame />
      </div>
    </Section>
  );
}

// --- Node states (for /architecture) -----------------------------------------

const STATE_GALLERY = [
  {
    caption: "Healthy, capacity mode",
    data: marketingNode({
      id: "s1",
      type: "cache",
      name: "Redis",
      technology: "Redis 7",
      status: "healthy",
      metric: { label: "memory", value: "42%" },
      utilization: 0.42,
      capacity: true,
    }),
  },
  {
    caption: "Warning with bottleneck badge",
    data: marketingNode({
      id: "s2",
      type: "database",
      name: "PostgreSQL",
      technology: "PostgreSQL 16",
      status: "warning",
      metric: { label: "connections", value: "82%" },
      utilization: 0.82,
      badges: [{ label: "Bottleneck", tone: "warning" }],
      capacity: true,
    }),
  },
  {
    caption: "Critical, reliability mode",
    data: marketingNode({
      id: "s3",
      type: "service",
      name: "Payment Service",
      technology: "Java",
      status: "critical",
      badges: [{ label: "SPOF", tone: "danger" }],
    }),
  },
  {
    caption: "AI proposal preview",
    data: marketingNode({
      id: "s4",
      type: "cache",
      name: "Redis",
      technology: "Cache-aside",
      preview: "added",
    }),
  },
] as const;

export function NodeStatesGallery() {
  return (
    <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4 [&_[data-status]]:w-full">
      {STATE_GALLERY.map((item) => (
        <li key={item.caption} className="flex flex-col gap-2">
          <ArchitectureNodeCard data={item.data} />
          <p className="text-xs text-muted">{item.caption}</p>
        </li>
      ))}
    </ul>
  );
}

/**
 * Product sections for the marketing pages: brownfield discovery, evidence-driven AI,
 * the visual trust model (spec §123–124) and product "screenshots" composed from the
 * real UI components (no external images).
 */
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { MockFrame, Section, SectionHeader } from "./Section";

export { BrownfieldSection } from "./brownfield-section";
export { NodeStatesGallery, ScreenshotsSection } from "./product-screenshots";

// --- Trust model ------------------------------------------------------------

const TRUST: ReadonlyArray<{ tag: React.ReactNode; title: string; body: string }> = [
  {
    tag: <ProvenanceTag kind="fact" />,
    title: "What the system is",
    body: "Components, connections and requirements you entered. Neutral.",
  },
  {
    tag: <ProvenanceTag kind="ai" />,
    title: "What the system thinks",
    body: "AI recommendations, always a proposal with a diff. Marked ✦ in mint.",
  },
  {
    tag: <ProvenanceTag kind="calculated" />,
    title: "What the system calculated",
    body: "Deterministic engine output: capacity, envelopes, costs. Blue.",
  },
  {
    tag: <StatusBadge status="warning" />,
    title: "Warning",
    body: "Close to a limit or a medium-severity finding. Amber, with an icon.",
  },
  {
    tag: <StatusBadge status="critical" />,
    title: "Critical finding",
    body: "Over a limit or a failure mode that takes the system down. Red, with an icon.",
  },
  {
    tag: <ProvenanceTag kind="evidence" />,
    title: "Evidence",
    body: "The inputs, formula and rule behind a number, in a secondary panel.",
  },
];

export function TrustModel({ headingLevel = 3 }: { headingLevel?: 2 | 3 }) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className="overflow-hidden rounded-lg border border-default bg-surface">
      <Heading className="label-caps border-b border-default px-4 py-3">Visual trust model</Heading>
      <ul className="grid gap-px bg-default sm:grid-cols-2 lg:grid-cols-3">
        {TRUST.map((item) => (
          <li key={item.title} className="flex flex-col gap-2 bg-surface p-4">
            <span>{item.tag}</span>
            <p className="text-sm font-medium text-fg">{item.title}</p>
            <p className="text-xs text-fg-secondary">{item.body}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- Evidence-driven AI -----------------------------------------------------

function DiffLine({
  symbol,
  tone,
  label,
  children,
}: {
  symbol: string;
  tone: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <li className="flex items-baseline gap-2 text-sm">
      <span aria-hidden className={cn("tabular w-3 shrink-0 font-semibold", tone)}>
        {symbol}
      </span>
      <span className="sr-only">{label}: </span>
      <span className="min-w-0 text-fg">{children}</span>
    </li>
  );
}

export function ProposalMock() {
  return (
    <MockFrame
      title="An AI proposal: a diff, its calculated impact and the evidence behind it."
      path="food-delivery / architecture ?proposal=prp_218"
    >
      <div className="flex flex-col">
        <div className="flex items-center gap-2 border-b border-default px-4 py-2.5">
          <ProvenanceTag kind="ai" />
          <span className="truncate text-xs text-muted">“Prepare for 2M daily users”</span>
        </div>
        <div className="flex flex-col gap-4 px-4 py-3">
          <p className="text-sm font-medium text-fg">
            Add a read replica and a Redis cache in front of PostgreSQL.
          </p>
          <section className="flex flex-col gap-1.5">
            <p className="label-caps">Changes</p>
            <ul className="flex flex-col gap-1">
              <DiffLine symbol="+" tone="text-accent-fg" label="Add">
                Redis <span className="text-muted">· Cache-aside</span>
              </DiffLine>
              <DiffLine symbol="+" tone="text-accent-fg" label="Add">
                Read replica <span className="text-muted">· PostgreSQL</span>
              </DiffLine>
              <DiffLine symbol="~" tone="text-warning-fg" label="Change">
                API <span className="text-muted">· replicas</span> <span className="tabular">3 → 4</span>
              </DiffLine>
            </ul>
          </section>
          <section className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-2">
              <p className="label-caps">Impact</p>
              <ProvenanceTag kind="calculated" label="Capacity engine" />
            </div>
            <ul className="flex flex-col gap-1.5">
              {[
                ["PostgreSQL queries", "148% → 41%"],
                ["Max supported DAU", "1.4M → 5.2M"],
                ["Cost", "$410 → $560 /month"],
              ].map(([metric, value]) => (
                <li key={metric} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-fg-secondary">{metric}</span>
                  <span className="tabular text-fg">{value}</span>
                </li>
              ))}
            </ul>
          </section>
          <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-surface-2 px-2.5 py-2">
            <span className="text-xs text-fg-secondary">
              <span className="tabular">3</span> evidence items
            </span>
            {["ev_pg_qps", "ev_cache_hit", "ev_cost"].map((id) => (
              <span
                key={id}
                className="tabular rounded-sm border border-default bg-surface px-1.5 text-2xs text-fg-secondary"
              >
                {id}
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-default px-4 py-2.5">
          <p className="text-2xs text-muted">Nothing changes until you apply.</p>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost">
              Reject
            </Button>
            <Button size="sm" variant="primary">
              Apply
            </Button>
          </div>
        </div>
      </div>
    </MockFrame>
  );
}

const AI_RULES = [
  ["Proposals, not edits", "Every AI change is a diff you review on the canvas. Apply, edit or reject."],
  ["Numbers come from engines", "Impact is recalculated by the capacity engine, never written by the model."],
  ["Evidence is inspectable", "Each claim links to the inputs, formula and rule that produced it."],
] as const;

export function EvidenceAiSection() {
  return (
    <Section labelledBy="ai-title">
      <SectionHeader
        id="ai-title"
        eyebrow="Evidence-driven AI"
        title="AI proposes. You review the diff. Evidence decides."
        description="The assistant is a capability, not the product. It never changes your architecture directly and never invents a number."
      />
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="flex flex-col gap-6">
          <dl className="flex flex-col gap-5">
            {AI_RULES.map(([term, detail]) => (
              <div key={term} className="flex flex-col gap-1 border-l-2 border-default pl-4">
                <dt className="text-sm font-semibold text-fg">{term}</dt>
                <dd className="text-sm text-fg-secondary">{detail}</dd>
              </div>
            ))}
          </dl>
        </div>
        <ProposalMock />
      </div>
      <div className="mt-10">
        <TrustModel />
      </div>
    </Section>
  );
}

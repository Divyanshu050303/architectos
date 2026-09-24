import { ArrowDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Section, SectionHeader } from "@/features/marketing/components/Section";
import { CtaSection, DemoSection, PageIntro } from "@/features/marketing/components/sections";
import { marketingMetadata } from "@/features/marketing/metadata";

export const metadata = marketingMetadata({
  title: "Simulation",
  description:
    "Fail a dependency or spike traffic and follow the effect through the architecture: impact, affected components, latency and error rate.",
});

const CASCADE = [
  "Failure",
  "Dependency",
  "Load increase",
  "Resource pressure",
  "Latency",
  "Potential failure",
] as const;

const RESULTS = [
  ["Impact", "High"],
  ["Affected components", "API, PostgreSQL, Order Service"],
  ["P99 latency", "320 ms → 1.8 s"],
  ["Error rate", "0.2% → 12.4%"],
  ["Cascading failure", "Potential"],
] as const;

export default function SimulationPage() {
  return (
    <>
      <PageIntro
        eyebrow="Simulation"
        title="Break it on the canvas, not in production."
        description="Choose a scenario — a database failure, a traffic spike, a slow dependency — and follow the effect through every component that depends on it."
      >
        <Badge>Planned · capacity analysis is available today</Badge>
      </PageIntro>
      <DemoSection />
      <Section labelledBy="cascade-title" tone="muted">
        <SectionHeader
          id="cascade-title"
          eyebrow="How a simulation reads"
          title="Cause first, consequence last."
          description="Results are shown as a chain of graph state changes, with subtle transitions that are switched off when you prefer reduced motion."
        />
        <div className="grid gap-8 lg:grid-cols-2">
          <ol className="flex flex-col items-start">
            {CASCADE.map((step, i) => (
              <li key={step} className="flex flex-col items-start">
                <span className="rounded-sm border border-default bg-surface px-3 py-1.5 text-sm text-fg">
                  <span className="tabular mr-2 text-2xs text-muted">{i + 1}</span>
                  {step}
                </span>
                {i < CASCADE.length - 1 ? (
                  <ArrowDown aria-hidden className="my-1 ml-4 size-3.5 text-muted" />
                ) : null}
              </li>
            ))}
          </ol>
          <div className="rounded-lg border border-default bg-surface">
            <div className="flex items-center justify-between gap-2 border-b border-default px-4 py-3">
              <h3 className="label-caps">Example result: PostgreSQL failure</h3>
              <Badge>Illustrative</Badge>
            </div>
            <dl className="divide-y divide-default px-4">
              {RESULTS.map(([term, value]) => (
                <div key={term} className="flex items-baseline justify-between gap-4 py-2.5 text-sm">
                  <dt className="text-fg-secondary">{term}</dt>
                  <dd className="tabular text-right text-fg">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </Section>
      <CtaSection />
    </>
  );
}

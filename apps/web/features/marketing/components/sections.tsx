/**
 * Landing-page sections (spec §118). Server components: static copy composed from the
 * design system. Anything interactive lives in ArchitectureDemo.
 */
import {
  ArrowRight,
  FlaskConical,
  GitBranch,
  LineChart,
  ListChecks,
  type LucideIcon,
  ScanSearch,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { ArchitectureDemo } from "./ArchitectureDemo";
import { Section, SectionHeader, TwoToneTitle } from "./Section";

export { PRICING, PricingSection, type PricingTier, PricingTiers } from "./pricing-sections";
export { TechArchitectureSection } from "./tech-architecture-section";

// --- Interactive demo -------------------------------------------------------

export function DemoSection() {
  return (
    <Section id="demo" labelledBy="demo-title">
      <SectionHeader
        id="demo-title"
        eyebrow="Interactive model"
        title="Change the load. Watch the architecture answer."
        description="Raise daily active users past one million without a cache and the PostgreSQL primary crosses its warning threshold. Add a cache, a read replica or API replicas and see which component becomes the next limit."
      />
      <ArchitectureDemo />
    </Section>
  );
}

// --- Problem ----------------------------------------------------------------

const PROBLEMS: ReadonlyArray<{ title: string; body: string; Icon: LucideIcon }> = [
  {
    title: "Diagrams carry no numbers",
    body: "A box labelled PostgreSQL says nothing about the 8,000 writes per second it will receive at peak, or when that becomes a problem.",
    Icon: LineChart,
  },
  {
    title: "Reviews happen too late",
    body: "Single points of failure, missing timeouts and unbounded retries are usually found in an incident review, not a design review.",
    Icon: TriangleAlert,
  },
  {
    title: "AI answers without evidence",
    body: "A chat reply that says “add Kafka” cannot be checked, versioned or compared with the architecture you already have.",
    Icon: ScanSearch,
  },
];

export function ProblemSection() {
  return (
    <Section labelledBy="problem-title" tone="muted">
      <SectionHeader
        id="problem-title"
        eyebrow="The problem"
        title="Architecture decisions are made on whiteboards and verified in production."
      />
      <ul className="grid gap-px overflow-hidden rounded-lg border border-default bg-default md:grid-cols-3">
        {PROBLEMS.map(({ title, body, Icon }) => (
          <li key={title} className="flex flex-col gap-3 bg-surface p-6">
            <Icon aria-hidden className="size-4 text-muted" />
            <h3 className="text-base font-semibold text-fg">{title}</h3>
            <p className="text-sm text-fg-secondary">{body}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

// --- Lifecycle --------------------------------------------------------------

export interface LifecycleStage {
  name: string;
  summary: string;
  output: string;
  Icon: LucideIcon;
  available: boolean;
}

export const LIFECYCLE: readonly LifecycleStage[] = [
  {
    name: "Design",
    summary: "Requirements become a versioned architecture model on a canvas you can edit.",
    output: "Architecture version",
    Icon: Workflow,
    available: true,
  },
  {
    name: "Validate",
    summary: "Rules check reliability, security and observability, and explain every finding.",
    output: "Findings + evidence",
    Icon: ListChecks,
    available: true,
  },
  {
    name: "Simulate",
    summary: "Fail a dependency or spike traffic and follow the cascade through the graph.",
    output: "Simulation result",
    Icon: FlaskConical,
    available: false,
  },
  {
    name: "Scale",
    summary: "Utilisation per component, the next bottleneck and the operating envelope.",
    output: "Capacity analysis",
    Icon: LineChart,
    available: true,
  },
  {
    name: "Evolve",
    summary: "Plan V2 and V3 with the trigger, cost and migration path for each change.",
    output: "Evolution plan",
    Icon: GitBranch,
    available: false,
  },
];

export function LifecycleSection() {
  return (
    <Section labelledBy="lifecycle-title">
      <SectionHeader
        id="lifecycle-title"
        eyebrow="Architecture lifecycle"
        title="One model from first sketch to the next migration."
        description="Every stage reads and writes the same architecture model, so a finding, a capacity number and a proposal always refer to the same components."
      />
      <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {LIFECYCLE.map((stage, index) => (
          <li
            key={stage.name}
            className="relative flex flex-col gap-3 rounded-md border border-default bg-surface p-4"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="tabular text-2xs text-muted">{String(index + 1).padStart(2, "0")}</span>
              {stage.available ? null : <Badge>Planned</Badge>}
            </div>
            <div className="flex items-center gap-2">
              <stage.Icon aria-hidden className="size-4 text-accent-fg" />
              <h3 className="text-base font-semibold text-fg">{stage.name}</h3>
            </div>
            <p className="flex-1 text-sm text-fg-secondary">{stage.summary}</p>
            <p className="border-t border-default pt-3 text-xs text-muted">
              <span className="sr-only">Output: </span>
              <span className="tabular">→ {stage.output}</span>
            </p>
            {index < LIFECYCLE.length - 1 ? (
              <ArrowRight
                aria-hidden
                className="absolute top-1/2 -right-3 z-10 hidden size-3 -translate-y-1/2 text-muted lg:block"
              />
            ) : null}
          </li>
        ))}
      </ol>
    </Section>
  );
}

// --- CTA --------------------------------------------------------------------

export function CtaSection() {
  return (
    <section aria-labelledby="cta-title" className="border-t border-default bg-surface">
      <div className="mx-auto flex w-full max-w-6xl flex-col items-start gap-6 px-4 py-16 sm:px-6 sm:py-20 md:flex-row md:items-center md:justify-between">
        <div className="flex max-w-xl flex-col gap-3">
          <h2 id="cta-title" className="headline text-3xl text-fg sm:text-4xl">
            Find the bottleneck <span className="text-muted">before your users do.</span>
          </h2>
          <p className="text-base text-fg-secondary">
            Start from requirements or an existing diagram. The first capacity analysis takes minutes.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button asChild variant="primary" className="cta-depth h-11 rounded-md px-5">
            <Link href="/dashboard">
              Start designing
              <ArrowRight aria-hidden className="size-4" />
            </Link>
          </Button>
          <Button asChild variant="secondary" className="h-11 rounded-md px-5">
            <Link href="/docs">Read the docs</Link>
          </Button>
        </div>
      </div>
    </section>
  );
}

// --- Page intro for secondary marketing pages -------------------------------

export function PageIntro({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <section aria-labelledby="page-title" className="bg-background">
      <div className="mx-auto w-full max-w-6xl px-4 pt-16 pb-12 sm:px-6 sm:pt-20">
        <p className="eyebrow mb-5">{eyebrow}</p>
        <h1 id="page-title" className="headline max-w-3xl text-4xl text-fg sm:text-5xl">
          <TwoToneTitle>{title}</TwoToneTitle>
        </h1>
        <p className="mt-4 max-w-2xl text-lg text-pretty text-fg-secondary">{description}</p>
        {children ? <div className="mt-8">{children}</div> : null}
      </div>
    </section>
  );
}

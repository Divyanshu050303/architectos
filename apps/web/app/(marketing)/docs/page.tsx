import Link from "next/link";

import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Button } from "@/components/ui/button";
import { TrustModel } from "@/features/marketing/components/product-sections";
import { PageIntro } from "@/features/marketing/components/sections";
import { marketingMetadata } from "@/features/marketing/metadata";

export const metadata = marketingMetadata({
  title: "Docs",
  description:
    "Get started with ArchitectOS: create a system, describe requirements, analyse capacity, review findings and apply AI proposals.",
});

const START = [
  ["Create a system", "From the dashboard, name the system you want to design."],
  [
    "Describe requirements",
    "Daily active users, peak factor, read/write mix, latency and availability targets.",
  ],
  ["Shape the architecture", "Add components and connections on the canvas, or ask for a proposal."],
  [
    "Analyse capacity",
    "Switch the canvas to Capacity mode or open the Capacity view for utilisation and the operating envelope.",
  ],
  [
    "Review findings",
    "Open Validation. Each finding can be explained, located, fixed, ignored or recorded as an ADR.",
  ],
  [
    "Record decisions",
    "Capture trade-offs as architecture decision records linked to the components they affect.",
  ],
] as const;

const CONCEPTS = [
  {
    term: "Architecture model",
    detail:
      "Components (nodes) and dependencies (edges) with configuration. The single source every analysis reads.",
  },
  {
    term: "Architecture version",
    detail:
      "An immutable snapshot created when you save. Moving nodes on the canvas never creates a version.",
  },
  {
    term: "Analysis mode",
    detail:
      "A lens on the same canvas: topology, capacity or reliability today; security, cost and simulation later.",
  },
  {
    term: "Capacity analysis",
    detail:
      "Per-component utilisation, the bottleneck and the operating envelope, calculated by the capacity engine.",
  },
  {
    term: "Finding",
    detail: "A validation result with severity, location, why it matters and a recommendation.",
  },
  {
    term: "Evidence",
    detail: "The inputs, formula or rule behind a number or finding. Open it with “Why?” or “Explain”.",
  },
  {
    term: "Proposal",
    detail:
      "An AI-suggested change expressed as a diff. It is previewed on the canvas and applied only by you.",
  },
  {
    term: "Decision record (ADR)",
    detail: "Context, decision and consequences for a trade-off, linked to components and findings.",
  },
] as const;

const TOC = [
  { id: "getting-started", label: "Getting started" },
  { id: "concepts", label: "Concepts" },
  { id: "trust", label: "Reading the interface" },
] as const;

export default function DocsPage() {
  return (
    <>
      <PageIntro
        eyebrow="Docs"
        title="Documentation"
        description="How ArchitectOS models a system, where each number comes from, and how to go from requirements to a validated architecture."
      />
      <div className="mx-auto grid w-full max-w-6xl gap-10 px-4 pb-20 sm:px-6 lg:grid-cols-[12rem_minmax(0,1fr)]">
        <nav aria-label="On this page" className="lg:sticky lg:top-20 lg:self-start">
          <p className="label-caps mb-2">On this page</p>
          <ul className="flex flex-col gap-1 border-l border-default">
            {TOC.map((item) => (
              <li key={item.id}>
                <a
                  href={`#${item.id}`}
                  className="-ml-px block border-l border-transparent py-1 pl-3 text-sm text-fg-secondary hover:border-strong hover:text-fg"
                >
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-col gap-16">
          <section aria-labelledby="getting-started" className="scroll-mt-20">
            <h2 id="getting-started" className="mb-6 text-xl font-semibold tracking-tight text-fg">
              Getting started
            </h2>
            <ol className="flex flex-col divide-y divide-default rounded-lg border border-default bg-surface">
              {START.map(([title, body], i) => (
                <li key={title} className="flex gap-4 px-4 py-3.5">
                  <span className="tabular mt-0.5 text-xs text-muted">{String(i + 1).padStart(2, "0")}</span>
                  <div className="flex flex-col gap-0.5">
                    <h3 className="text-sm font-semibold text-fg">{title}</h3>
                    <p className="text-sm text-fg-secondary">{body}</p>
                  </div>
                </li>
              ))}
            </ol>
            <Button asChild variant="primary" className="mt-6">
              <Link href="/dashboard">Open the app</Link>
            </Button>
          </section>

          <section aria-labelledby="concepts" className="scroll-mt-20">
            <h2 id="concepts" className="mb-6 text-xl font-semibold tracking-tight text-fg">
              Concepts
            </h2>
            <dl className="grid gap-x-8 gap-y-6 sm:grid-cols-2">
              {CONCEPTS.map((c) => (
                <div key={c.term} className="flex flex-col gap-1">
                  <dt className="text-sm font-semibold text-fg">{c.term}</dt>
                  <dd className="text-sm text-fg-secondary">{c.detail}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section aria-labelledby="trust" className="scroll-mt-20">
            <h2 id="trust" className="mb-3 text-xl font-semibold tracking-tight text-fg">
              Reading the interface
            </h2>
            <p className="mb-6 max-w-2xl text-sm text-fg-secondary">
              Every value is tagged with where it came from. A number marked{" "}
              <ProvenanceTag kind="calculated" className="align-middle" /> was produced by an engine; anything
              marked <ProvenanceTag kind="ai" className="align-middle" /> is a suggestion until you apply it.
            </p>
            <TrustModel />
          </section>
        </div>
      </div>
    </>
  );
}

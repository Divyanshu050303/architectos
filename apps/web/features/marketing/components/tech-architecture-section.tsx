/** Technical architecture section for the marketing pages (spec §122). */
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

import { Section, SectionHeader } from "./Section";

// --- Technical architecture (spec §122) -------------------------------------

function DiagramBox({
  children,
  className,
  calculated = false,
}: {
  children: React.ReactNode;
  className?: string;
  /** Deterministic engines use the "calculated" (info) treatment of the trust model. */
  calculated?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-sm border px-3 py-2 text-center text-xs font-medium",
        calculated ? "border-info/60 bg-info-soft text-info-fg" : "border-strong bg-surface text-fg",
        className,
      )}
    >
      {children}
    </div>
  );
}

function DiagramArrow() {
  return (
    <div aria-hidden className="flex justify-center py-1.5 text-muted">
      <span className="h-5 w-0 border-l border-control" />
    </div>
  );
}

const TECH_FACTS = [
  "Next.js App Router, React 19, TypeScript in strict mode.",
  "Every API response is parsed with a schema before it reaches a component.",
  "Server state in TanStack Query; only UI state in Zustand.",
  "The canvas is React Flow, driven by semantic architecture commands with undo and redo.",
  "Capacity, validation and simulation run in backend engines. The browser formats their results; it never computes them.",
] as const;

export function TechArchitectureSection() {
  return (
    <Section labelledBy="tech-title" tone="muted">
      <SectionHeader
        id="tech-title"
        eyebrow="Technical architecture"
        title="Built with the same discipline it asks of you."
      />
      <div className="grid gap-10 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <figure className="m-0 rounded-lg border border-default bg-surface p-4 sm:p-6">
          <figcaption className="label-caps mb-4">Frontend architecture</figcaption>
          <div className="flex flex-col">
            <DiagramBox className="mx-auto w-40">Next.js</DiagramBox>
            <DiagramArrow />
            <div className="grid grid-cols-3 items-start gap-2">
              <DiagramBox>App Router</DiagramBox>
              <DiagramBox>Components</DiagramBox>
              <div className="flex flex-col gap-1">
                <DiagramBox>Features</DiagramBox>
                <ul className="tabular flex flex-col gap-0.5 pl-2 text-2xs text-muted">
                  <li>├ Architecture</li>
                  <li>├ Capacity</li>
                  <li>├ Validation</li>
                  <li>├ Simulation</li>
                  <li>└ Evolution</li>
                </ul>
              </div>
            </div>
            <DiagramArrow />
            <DiagramBox className="mx-auto w-40">UI system</DiagramBox>
            <DiagramArrow />
            <div className="grid grid-cols-3 gap-2">
              <DiagramBox>TanStack Query</DiagramBox>
              <DiagramBox>Zustand</DiagramBox>
              <DiagramBox>Hooks</DiagramBox>
            </div>
            <DiagramArrow />
            <DiagramBox className="mx-auto w-40">API client</DiagramBox>
            <DiagramArrow />
            <DiagramBox calculated className="mx-auto w-40">
              FastAPI engines
            </DiagramBox>
          </div>
        </figure>
        <ul className="flex flex-col gap-4">
          {TECH_FACTS.map((fact) => (
            <li key={fact} className="flex gap-3 text-sm text-fg-secondary">
              <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-fg" />
              {fact}
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}

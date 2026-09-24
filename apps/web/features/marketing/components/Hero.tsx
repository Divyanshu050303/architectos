import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export const HERO_FACTS = [
  { term: "Capacity", detail: "Calculated by a deterministic engine, not estimated by a language model." },
  { term: "Validation", detail: "Rule-based findings, each traceable to the evidence behind it." },
  { term: "AI", detail: "Proposes changes as diffs. Nothing is applied until you review it." },
] as const;

/** Hero (spec §119). Plain claims, no superlatives. */
export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="relative isolate overflow-hidden bg-background">
      <div aria-hidden className="hero-grid absolute inset-0 -z-10 opacity-60" />
      <div className="mx-auto w-full max-w-6xl px-4 pt-20 pb-12 sm:px-6 sm:pt-28 sm:pb-16">
        <p className="eyebrow mb-6">Architecture engineering platform</p>
        <h1 id="hero-title" className="headline max-w-4xl text-5xl leading-[1.02] text-fg sm:text-7xl">
          Design systems <span className="text-muted">before they break.</span>
        </h1>
        <p className="mt-5 max-w-2xl text-lg text-pretty text-fg-secondary">
          Turn requirements into architectures you can measure, validate, simulate, and evolve.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button asChild variant="primary" className="cta-depth h-11 rounded-md px-5 text-sm">
            <Link href="/dashboard">
              Start designing
              <ArrowRight aria-hidden className="size-4" />
            </Link>
          </Button>
          <Button asChild variant="secondary" className="h-11 rounded-md px-5 text-sm">
            <Link href="#demo">Try the live model</Link>
          </Button>
        </div>
        <dl className="mt-14 grid max-w-4xl gap-6 border-t border-default pt-6 sm:grid-cols-3">
          {HERO_FACTS.map((fact) => (
            <div key={fact.term} className="flex flex-col gap-1.5">
              <dt className="eyebrow">{fact.term}</dt>
              <dd className="text-sm text-fg-secondary">{fact.detail}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

/** Pricing tiers and the landing-page pricing section. */
import { Check } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { Section, SectionHeader } from "./Section";

// --- Pricing ----------------------------------------------------------------

export interface PricingTier {
  name: string;
  price: string;
  note: string;
  features: readonly string[];
  cta: { label: string; href: "/dashboard" | "/docs" };
  featured?: boolean;
}

/** Pricing is not final; tiers describe scope, not invented prices. */
export const PRICING: readonly PricingTier[] = [
  {
    name: "Individual",
    price: "Free",
    note: "During early access",
    features: [
      "Unlimited architectures",
      "Capacity analysis",
      "Validation findings",
      "Printable architecture reports",
    ],
    cta: { label: "Start designing", href: "/dashboard" },
  },
  {
    name: "Team",
    price: "Early access",
    note: "Per seat, billed monthly",
    features: [
      "Everything in Individual",
      "Shared projects and ADRs",
      "AI proposals with evidence",
      "Version history and comparison",
    ],
    cta: { label: "Start designing", href: "/dashboard" },
    featured: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    note: "Annual agreement",
    features: [
      "Everything in Team",
      "Brownfield discovery",
      "Single sign-on and audit log",
      "Self-hosted engines",
    ],
    cta: { label: "Read the docs", href: "/docs" },
  },
];

export function PricingTiers({ headingLevel = 3 }: { headingLevel?: 2 | 3 }) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <ul className="grid gap-4 md:grid-cols-3">
      {PRICING.map((tier) => (
        <li
          key={tier.name}
          className={cn(
            "flex flex-col gap-5 rounded-md border bg-surface p-6",
            tier.featured ? "border-accent-strong" : "border-default",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <Heading className="text-base font-semibold text-fg">{tier.name}</Heading>
            {tier.featured ? <Badge tone="accent">Most teams</Badge> : null}
          </div>
          <div>
            <p className="text-2xl font-semibold tracking-tight text-fg">{tier.price}</p>
            <p className="mt-1 text-xs text-muted">{tier.note}</p>
          </div>
          <ul className="flex flex-1 flex-col gap-2 border-t border-default pt-5">
            {tier.features.map((feature) => (
              <li key={feature} className="flex gap-2 text-sm text-fg-secondary">
                <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-fg" />
                {feature}
              </li>
            ))}
          </ul>
          <Button asChild variant={tier.featured ? "primary" : "secondary"}>
            <Link href={tier.cta.href}>{tier.cta.label}</Link>
          </Button>
        </li>
      ))}
    </ul>
  );
}

export function PricingSection() {
  return (
    <Section labelledBy="pricing-title">
      <SectionHeader
        id="pricing-title"
        eyebrow="Pricing"
        title="Free while in early access."
        description="Prices for Team and Enterprise will be published before general availability. Nothing you build now will be locked."
        aside={
          <Link
            href="/pricing"
            className="text-sm font-medium text-fg-secondary underline-offset-4 hover:text-fg hover:underline"
          >
            Compare plans
          </Link>
        }
      />
      <PricingTiers />
    </Section>
  );
}

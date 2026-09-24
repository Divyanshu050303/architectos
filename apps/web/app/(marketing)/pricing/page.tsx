import { Section, SectionHeader } from "@/features/marketing/components/Section";
import { CtaSection, PageIntro, PricingTiers } from "@/features/marketing/components/sections";
import { marketingMetadata } from "@/features/marketing/metadata";

export const metadata = marketingMetadata({
  title: "Pricing",
  description:
    "ArchitectOS is free for individuals during early access. Team and Enterprise pricing will be published before general availability.",
});

const FAQ = [
  [
    "What happens to my work after early access?",
    "Your architectures, versions and decision records stay yours. You can print or save the architecture report as PDF at any time.",
  ],
  [
    "Are AI proposals billed separately?",
    "No. Proposals are part of the Team plan. Capacity and validation run on deterministic engines and are never metered per request.",
  ],
  [
    "Is capacity estimated by AI?",
    "No. Capacity, envelopes and costs come from deterministic engines. AI only proposes changes, which are recalculated before you see their impact.",
  ],
] as const;

export default function PricingPage() {
  return (
    <>
      <PageIntro
        eyebrow="Pricing"
        title="Free while in early access."
        description="Use ArchitectOS for real systems today. Team and Enterprise prices will be published before general availability."
      />
      <Section labelledBy="plans-title">
        <h2 id="plans-title" className="sr-only">
          Plans
        </h2>
        <PricingTiers />
      </Section>
      <Section labelledBy="faq-title" tone="muted">
        <SectionHeader id="faq-title" title="Questions" />
        <dl className="grid gap-8 md:grid-cols-3">
          {FAQ.map(([question, answer]) => (
            <div key={question} className="flex flex-col gap-2">
              <dt className="text-sm font-semibold text-fg">{question}</dt>
              <dd className="text-sm text-fg-secondary">{answer}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <CtaSection />
    </>
  );
}

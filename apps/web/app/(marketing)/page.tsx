import { Hero } from "@/features/marketing/components/Hero";
import {
  BrownfieldSection,
  EvidenceAiSection,
  ScreenshotsSection,
} from "@/features/marketing/components/product-sections";
import {
  CtaSection,
  DemoSection,
  LifecycleSection,
  PricingSection,
  ProblemSection,
  TechArchitectureSection,
} from "@/features/marketing/components/sections";
import { marketingMetadata } from "@/features/marketing/metadata";

export const metadata = marketingMetadata({
  title: "ArchitectOS · Design systems before they break",
  description:
    "Turn requirements into architectures you can measure, validate, simulate, and evolve. Deterministic capacity analysis, explainable findings and AI proposals you review as diffs.",
  absoluteTitle: true,
});

/** Landing page, structured per spec §118. */
export default function LandingPage() {
  return (
    <>
      <Hero />
      <DemoSection />
      <ProblemSection />
      <LifecycleSection />
      <BrownfieldSection />
      <EvidenceAiSection />
      <ScreenshotsSection />
      <TechArchitectureSection />
      <PricingSection />
      <CtaSection />
    </>
  );
}

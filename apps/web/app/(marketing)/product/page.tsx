import { Button } from "@/components/ui/button";
import { EvidenceAiSection, ScreenshotsSection } from "@/features/marketing/components/product-sections";
import { CtaSection, LifecycleSection, PageIntro } from "@/features/marketing/components/sections";
import { marketingMetadata } from "@/features/marketing/metadata";
import Link from "next/link";

export const metadata = marketingMetadata({
  title: "Product",
  description:
    "Requirements, architecture canvas, capacity analysis, validation and decision records in one engineering workspace.",
});

export default function ProductPage() {
  return (
    <>
      <PageIntro
        eyebrow="Product"
        title="The architecture is the product."
        description="A living model of your system. Every analysis — capacity, validation, reliability — is a lens on the same canvas, not a separate dashboard."
      >
        <Button asChild variant="primary" className="h-10 px-4">
          <Link href="/dashboard">Start designing</Link>
        </Button>
      </PageIntro>
      <LifecycleSection />
      <ScreenshotsSection />
      <EvidenceAiSection />
      <CtaSection />
    </>
  );
}

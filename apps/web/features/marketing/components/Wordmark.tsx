import { Wordmark as BrandWordmark } from "@/components/brand/Logo";
import { cn } from "@/lib/utils";

/** Same brand wordmark as the application top bar, so the two surfaces read as one product. */
export function Wordmark({ className, tagline = false }: { className?: string; tagline?: boolean }) {
  return <BrandWordmark className={cn("text-sm", className)} tagline={tagline} />;
}

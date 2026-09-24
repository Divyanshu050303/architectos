import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

/** Shown only when the toolbar is at least 64rem wide (a wrapper, so no display class clashes). */
export const WIDE_ONLY = "hidden items-center gap-0.5 @min-[64rem]:flex";

export function ToolbarGroup({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("flex shrink-0 items-center gap-0.5", className)}>{children}</div>;
}

export function Divider({ className }: { className?: string }) {
  return <Separator orientation="vertical" className={cn("mx-1 h-5", className)} />;
}

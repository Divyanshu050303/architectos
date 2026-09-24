import Image from "next/image";

import { cn } from "@/lib/utils";

/**
 * ArchitectOS brand mark (the ribbon "A" with ✦), from the official artwork.
 * public/brand/logo-mark.png is the 1024px transparent master; the UI uses the
 * 256px export. Icons in app/ and public/brand/ are rendered from the same master.
 * The mark is used flat in the product UI (no glow; spec §11, §102).
 */
export function LogoMark({
  size = 20,
  className,
  title,
}: {
  size?: number;
  className?: string;
  title?: string;
}) {
  return (
    <Image
      src="/brand/logo-mark-256.png"
      width={size}
      height={size}
      alt={title ?? ""}
      aria-hidden={title ? undefined : true}
      className={cn("shrink-0 select-none", className)}
      draggable={false}
      preload
    />
  );
}

export interface WordmarkProps {
  className?: string;
  /** Mark size in px; the text follows the surrounding font size. */
  size?: number;
  tagline?: boolean;
}

/**
 * Mark + live-text wordmark matching the lockup ("Architect" + "OS" in mint). Live text
 * rather than the lockup PNGs, which have white backgrounds and would break dark mode.
 */
export function Wordmark({ className, size = 20, tagline = false }: WordmarkProps) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <LogoMark size={size} />
      <span className="flex flex-col leading-none">
        <span className="font-semibold tracking-tight text-fg">
          Architect<span className="text-accent-fg">OS</span>
        </span>
        {tagline ? (
          <span className="mt-1 text-2xs font-medium tracking-[0.2em] text-muted uppercase">
            Design today. Scale tomorrow.
          </span>
        ) : null}
      </span>
    </span>
  );
}

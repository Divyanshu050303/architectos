import Link from "next/link";

import { MARKETING_NAV } from "../nav";
import { Wordmark } from "./Wordmark";

export function MarketingFooter() {
  return (
    <footer className="border-t border-default bg-background">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-10 sm:px-6 md:flex-row md:justify-between">
        <div className="flex max-w-xs flex-col gap-3">
          <Wordmark tagline />
          <p className="text-sm text-fg-secondary">
            Architecture engineering: design, validate, simulate, scale and evolve software systems.
          </p>
        </div>
        <nav aria-label="Footer" className="grid grid-cols-2 gap-x-12 gap-y-2 sm:grid-cols-3">
          {MARKETING_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-sm text-sm text-fg-secondary hover:text-fg"
            >
              {item.label}
            </Link>
          ))}
          <Link href="/dashboard" className="rounded-sm text-sm text-fg-secondary hover:text-fg">
            Open app
          </Link>
        </nav>
      </div>
      <div className="border-t border-default">
        <p className="mx-auto w-full max-w-6xl px-4 py-4 text-xs text-muted sm:px-6">
          © ArchitectOS. Figures on this site are illustrative unless marked as calculated.
        </p>
      </div>
    </footer>
  );
}
